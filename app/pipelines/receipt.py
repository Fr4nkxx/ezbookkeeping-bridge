import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.core.schemas import (
    BridgeResponse,
    ChoiceItem,
    ReceiptLlmOutput,
    TaskStatus,
    TransactionSummary,
)
from app.ez.catalog import AccountCandidate, CategoryCandidate, EzCatalog
from app.ez.client import EzClient
from app.ez.matching import find_default_cash_account, match_account, match_category, match_tags
from app.ez.transactions import SubmitStatus, TransactionDraft
from app.image.intake import ImagePayload
from app.pipelines.base import VisionPipeline, to_choices
from app.utils.amount import decimal_to_ez_int, format_amount_display, parse_decimal_amount
from app.utils.time_util import parse_local_datetime_to_unix, utc_offset_minutes_for_zone

CATEGORY_TYPE_BY_TXN = {
    "expense": 2,
    "income": 1,
    "transfer": 3,
}

EZ_TXN_TYPE = {
    "expense": EzClient.TRANSACTION_TYPE_EXPENSE,
    "income": EzClient.TRANSACTION_TYPE_INCOME,
    "transfer": EzClient.TRANSACTION_TYPE_TRANSFER,
}

_TYPE_LABEL = {"expense": "支出", "income": "收入", "transfer": "转账"}

_NEEDS_INPUT_MESSAGES = {
    "category": "无法确定分类，请改进提示词或截图信息后重试",
    "account": "无法确定账户",
    "destination_account": "无法确定转入账户",
    "amount": "无法确定金额",
    "currency": "账户币种与识别币种不一致",
    "tags": "标签存在歧义",
}

_PENDING_CATEGORY_NAME = "待分类"
_DEFAULT_TRANSFER_CATEGORY_NAME = "转账"


@dataclass
class Resolution:
    """收集解析过程中缺失的字段与候选项；missing 保留追加顺序，用于挑选首要提示。"""

    missing: list[str] = field(default_factory=list)
    choices: dict[str, list[ChoiceItem]] = field(default_factory=dict)

    def need(self, field_name: str, candidates: list[ChoiceItem] | None = None) -> None:
        self.missing.append(field_name)
        if candidates:
            self.choices[field_name] = candidates

    def has(self, field_name: str) -> bool:
        return field_name in self.missing


def resolve_amount(llm: ReceiptLlmOutput, res: Resolution) -> Decimal | None:
    try:
        return parse_decimal_amount(llm.amount)
    except ValueError:
        res.need("amount")
        return None


def resolve_source_account(
    catalog: EzCatalog,
    llm: ReceiptLlmOutput,
    txn_type: str,
    default_cash_name: str,
    res: Resolution,
) -> AccountCandidate | None:
    account: AccountCandidate | None = None
    source_name = llm.source_account
    if txn_type in ("expense", "income") and not source_name:
        account = find_default_cash_account(catalog, default_cash_name)
    if not account and source_name:
        match = match_account(catalog, source_name)
        if match.item:
            account = match.item  # type: ignore[assignment]
        elif match.ambiguous:
            res.need("account", to_choices(match.ambiguous))
    if not account and not res.has("account"):
        res.need("account")
    return account


def resolve_destination_account(
    catalog: EzCatalog,
    llm: ReceiptLlmOutput,
    res: Resolution,
) -> AccountCandidate | None:
    dest_account: AccountCandidate | None = None
    if llm.destination_account:
        match = match_account(catalog, llm.destination_account)
        if match.item:
            dest_account = match.item  # type: ignore[assignment]
        elif match.ambiguous:
            res.need("destination_account", to_choices(match.ambiguous))
    if not dest_account and not res.has("destination_account"):
        res.need("destination_account")
    return dest_account


def resolve_category(
    catalog: EzCatalog,
    llm: ReceiptLlmOutput,
    txn_type: str,
    res: Resolution,
) -> tuple[CategoryCandidate | None, str | None]:
    """返回 (分类, 回退说明)。收支匹配不到时回退「待分类」，转账回退「转账」。"""
    if txn_type == "transfer":
        return _resolve_transfer_category(catalog, llm, res)

    category_type = CATEGORY_TYPE_BY_TXN[txn_type]
    category: CategoryCandidate | None = None
    fallback_note: str | None = None
    if llm.category:
        match = match_category(catalog, llm.category, txn_type=category_type)
        if match.item:
            category = match.item  # type: ignore[assignment]
        elif match.ambiguous:
            res.need("category", to_choices(match.ambiguous))
    if not category and not res.has("category"):
        pending = match_category(catalog, _PENDING_CATEGORY_NAME, txn_type=category_type)
        if pending.item:
            category = pending.item  # type: ignore[assignment]
            fallback_note = f"未能匹配「{llm.category or '空'}」，已回退到待分类"
        elif pending.ambiguous:
            res.need("category", to_choices(pending.ambiguous))
        else:
            res.need("category")
    return category, fallback_note


def _resolve_transfer_category(
    catalog: EzCatalog,
    llm: ReceiptLlmOutput,
    res: Resolution,
) -> tuple[CategoryCandidate | None, str | None]:
    category_type = CATEGORY_TYPE_BY_TXN["transfer"]
    category: CategoryCandidate | None = None
    fallback_note: str | None = None
    if llm.category:
        match = match_category(catalog, llm.category, txn_type=category_type)
        if match.item:
            category = match.item  # type: ignore[assignment]
    if not category:
        match = match_category(catalog, _DEFAULT_TRANSFER_CATEGORY_NAME, txn_type=category_type)
        if match.item:
            category = match.item  # type: ignore[assignment]
            fallback_note = "未匹配到指定转账分类，已使用默认转账分类"
        elif match.ambiguous:
            res.need("category", to_choices(match.ambiguous))
    if not category and not res.has("category"):
        res.need("category")
    return category, fallback_note


def resolve_tags(catalog: EzCatalog, llm: ReceiptLlmOutput, res: Resolution) -> list[str]:
    if not llm.tags:
        return []
    resolved, _missing_tags, ambiguous_tags = match_tags(catalog, llm.tags)
    if ambiguous_tags:
        res.missing.append("tags")
        for name, items in ambiguous_tags.items():
            res.choices[f"tag:{name}"] = to_choices(items)
    return [t.id for t in resolved]


def resolve_currency(
    llm: ReceiptLlmOutput,
    account: AccountCandidate | None,
    res: Resolution,
) -> str | None:
    currency = llm.currency or (account.currency if account else None)
    if account and currency and account.currency != currency:
        res.need(
            "currency",
            [ChoiceItem(id=account.currency, name=account.currency, path=account.currency)],
        )
    return currency


def resolve_time(llm: ReceiptLlmOutput, timezone_name: str) -> tuple[int, bool]:
    """返回 (Unix 秒, 是否使用了当前时间)。"""
    if llm.time:
        try:
            return parse_local_datetime_to_unix(llm.time, timezone_name), False
        except ValueError:
            pass
    return int(time.time()), True


def needs_input_message(missing: list[str]) -> str:
    for key in missing:
        if key in _NEEDS_INPUT_MESSAGES:
            return _NEEDS_INPUT_MESSAGES[key]
    return "识别信息不完整，无法记账"


def format_success_message(
    txn_type: str,
    summary: TransactionSummary,
    *,
    category_reason: str | None = None,
    category_fallback_note: str | None = None,
    used_current_time: bool = False,
    attached_picture: bool = False,
) -> str:
    lines = [
        "✅ 记账成功",
        f"{_TYPE_LABEL.get(txn_type, txn_type)}：{summary.amount} 元",
        f"账户：{summary.account}",
    ]
    if summary.category:
        lines.append(f"类别：{summary.category}")
    if category_reason:
        lines.append(f"分类依据：{category_reason}")
    if category_fallback_note:
        lines.append(f"分类处理：{category_fallback_note}")
    if summary.description:
        lines.append(f"备注：{summary.description}")
    if used_current_time:
        lines.append("时间：截图未识别到可靠时间，已使用当前时间")
    if attached_picture:
        lines.append("附件：已上传截图")
    return "\n".join(lines)


class ReceiptPipeline(VisionPipeline[ReceiptLlmOutput]):
    """消费 / 收入 / 转账截图记账。"""

    kind = "receipt"
    prompt_file = "receipt.txt"
    instruction = "请根据图片输出 JSON，字段遵循系统说明。"
    output_schema = ReceiptLlmOutput

    async def resolve(
        self,
        request_id: str,
        parsed: ReceiptLlmOutput,
        catalog: EzCatalog,
        image: ImagePayload,
        geo_location: tuple[float, float] | None = None,
    ) -> BridgeResponse:
        llm = parsed
        recognition = llm.model_dump()
        txn_type = llm.type.strip().lower()
        if txn_type not in EZ_TXN_TYPE:
            return self._reply(request_id, TaskStatus.Rejected, "识别结果交易类型无效，已拒绝写入。", recognition)

        res = Resolution()
        amount = resolve_amount(llm, res)
        account = resolve_source_account(
            catalog, llm, txn_type, self.settings.default_cash_account_name, res
        )
        dest_account: AccountCandidate | None = None
        if txn_type == "transfer":
            dest_account = resolve_destination_account(catalog, llm, res)
            if account and dest_account and account.id == dest_account.id:
                return self._reply(
                    request_id, TaskStatus.Rejected, "识别为同一账户内流动，无需转账记录。", recognition
                )
        category, category_fallback_note = resolve_category(catalog, llm, txn_type, res)
        tag_ids = resolve_tags(catalog, llm, res)
        currency = resolve_currency(llm, account, res)
        time_unix, used_current_time = resolve_time(llm, self.settings.default_timezone)

        if res.missing:
            return BridgeResponse(
                status=TaskStatus.NeedsInput,
                request_id=request_id,
                message=needs_input_message(res.missing),
                missing_fields=sorted(set(res.missing)),
                choices=res.choices or None,
                recognition=recognition,
            )

        assert account is not None
        assert amount is not None

        ez_amount = decimal_to_ez_int(amount)
        if txn_type in ("expense", "income") and ez_amount < 0:
            return self._reply(
                request_id, TaskStatus.Rejected, "金额方向与类型不符。退款请识别为 income。", recognition
            )

        session_id = EzClient.new_client_session_id(request_id[:8])
        upload = await self._deps.gateway.upload_picture(
            image.jpeg_bytes,
            filename=f"receipt-{request_id[:8]}.jpg",
            client_session_id=f"{session_id}-pic",
        )
        if not upload.ok:
            return self._reply(
                request_id,
                TaskStatus.Failed,
                f"截图附件上传失败：{upload.error}。请确认 ez 已开启交易图片功能（EnableTransactionPictures）。",
                recognition,
            )

        description = llm.description or ""
        draft = self._build_draft(
            txn_type=txn_type,
            llm=llm,
            account=account,
            dest_account=dest_account,
            category=category,
            ez_amount=ez_amount,
            tag_ids=tag_ids,
            picture_ids=[upload.id],
            time_unix=time_unix,
            description=description,
            session_id=session_id,
            geo_location=geo_location,
        )

        submit = await self._deps.gateway.submit(draft)
        if not submit.ok:
            if submit.status == SubmitStatus.Timeout:
                message = submit.error or ""
            else:
                message = f"写入 ez 失败：{submit.error}"
            return BridgeResponse(status=TaskStatus.Failed, request_id=request_id, message=message)

        summary = TransactionSummary(
            id=submit.id,
            type=txn_type,
            amount=format_amount_display(amount),
            currency=currency or account.currency,
            account=account.path,
            category=category.path if category else None,
            description=description,
        )
        message = format_success_message(
            txn_type,
            summary,
            category_reason=(llm.category_reason or "").strip() or None,
            category_fallback_note=category_fallback_note,
            used_current_time=used_current_time,
            attached_picture=bool(draft.picture_ids),
        )
        return BridgeResponse(
            status=TaskStatus.Saved,
            request_id=request_id,
            message=message,
            transaction=summary,
            recognition=recognition,
        )

    def _build_draft(
        self,
        *,
        txn_type: str,
        llm: ReceiptLlmOutput,
        account: AccountCandidate,
        dest_account: AccountCandidate | None,
        category: CategoryCandidate | None,
        ez_amount: int,
        tag_ids: list[str],
        picture_ids: list[str],
        time_unix: int,
        description: str,
        session_id: str,
        geo_location: tuple[float, float] | None = None,
    ) -> TransactionDraft:
        source_amount = abs(ez_amount)
        draft = TransactionDraft(
            txn_type=EZ_TXN_TYPE[txn_type],
            category_id=category.id if category else "0",
            time_unix=time_unix,
            utc_offset=utc_offset_minutes_for_zone(self.settings.default_timezone, time_unix),
            source_account_id=account.id,
            source_amount=source_amount,
            comment=description,
            client_session_id=session_id,
            tag_ids=tag_ids,
            picture_ids=picture_ids,
            geo_location=geo_location,
        )
        if txn_type == "transfer":
            assert dest_account is not None
            draft.destination_account_id = dest_account.id
            if llm.destination_amount:
                draft.destination_amount = abs(decimal_to_ez_int(parse_decimal_amount(llm.destination_amount)))
            else:
                draft.destination_amount = source_amount
        return draft

    @staticmethod
    def _reply(
        request_id: str,
        status: TaskStatus,
        message: str,
        recognition: dict[str, Any] | None = None,
    ) -> BridgeResponse:
        return BridgeResponse(status=status, request_id=request_id, message=message, recognition=recognition)
