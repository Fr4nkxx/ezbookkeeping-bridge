"""Reconcile multiple screenshots against their matching ez accounts."""

import logging
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import httpx

from app.core.errors import EzApiError
from app.core.schemas import BalanceLlmOutput, BridgeResponse, TaskStatus, TransactionSummary
from app.ez.catalog import AccountCandidate, CategoryCandidate, load_catalog
from app.ez.client import EzClient
from app.ez.matching import find_default_cash_account, match_account
from app.ez.transactions import SubmitStatus, TransactionDraft
from app.image.intake import ImagePayload
from app.pipelines.balance import BalancePipeline
from app.pipelines.base import PipelineOutcome, to_choices
from app.utils.amount import ez_int_to_decimal, format_amount_display, parse_decimal_amount
from app.utils.time_util import utc_offset_minutes_for_zone

logger = logging.getLogger(__name__)


def _platform_key(value: str) -> str:
    normalized = value.strip().lower().replace(" ", "")
    for key, aliases in {
        "微信": ("微信", "wechat"),
        "支付宝": ("支付宝", "alipay"),
        "币安": ("币安", "binance"),
        "OKX": ("okx", "欧易"),
    }.items():
        if any(alias in normalized for alias in aliases):
            return key
    return normalized


@dataclass
class _Entry:
    index: int
    image: ImagePayload
    platform: str
    value: Decimal


@dataclass
class _Group:
    account: AccountCandidate
    adjust_kind: str
    entries: list[_Entry] = field(default_factory=list)
    target: Decimal = Decimal("0")
    delta: Decimal = Decimal("0")
    txn_type: int = 0
    category: CategoryCandidate | None = None
    ez_amount: int = 0

    def detail(self) -> str:
        return "、".join(
            f"{entry.platform} {format_amount_display(entry.value)}"
            for entry in self.entries
        )


class AggregateBalancePipeline(BalancePipeline):
    """Identify each screenshot, group by account, then reconcile each group."""

    kind = "balance_aggregate"
    prompt_file = "balance_aggregate.txt"

    async def run_images(self, *, request_id: str, images: list[ImagePayload]) -> PipelineOutcome:
        try:
            result = await self._reconcile_images(request_id, images)
        except Exception as exc:
            logger.exception("合计对账异常（request_id=%s）", request_id)
            result = BridgeResponse(
                status=TaskStatus.Failed,
                request_id=request_id,
                message=f"合计对账处理异常：{exc}",
            )
        notified = await self._deps.notifier.dispatch(
            self._deps.formatter.format(kind=self.kind, result=result)
        )
        return PipelineOutcome(result=result, notified=notified)

    @staticmethod
    def _needs_input(
        request_id: str, message: str, index: int | None = None,
        field: str = "account", choices: list[AccountCandidate] | None = None,
    ) -> BridgeResponse:
        return BridgeResponse(
            status=TaskStatus.NeedsInput,
            request_id=request_id,
            message=message,
            missing_fields=[f"images[{index}]" if index is not None else field],
            choices={"account": to_choices(choices)} if choices else None,
        )

    async def _reconcile_images(
        self, request_id: str, images: list[ImagePayload]
    ) -> BridgeResponse:
        catalog = await load_catalog(self._deps.ez)
        prompt = self._deps.prompts.build(
            self.prompt_file, catalog,
            "逐张识别截图的所属平台、应匹配的 ez 账户、当前总余额、币种和调整类型；只返回该张图片的 JSON。",
        )
        extracted: list[tuple[_Entry, BalanceLlmOutput]] = []
        seen_images: set[str] = set()
        seen_platforms: set[str] = set()
        for index, image in enumerate(images, start=1):
            if image.sha256 in seen_images:
                return self._needs_input(request_id, f"第 {index} 张截图与前面的截图重复", index)
            seen_images.add(image.sha256)
            parsed = await self._deps.vision.parse_image(
                system_prompt=prompt.system,
                user_prompt=f"{prompt.user}\n\n这是第 {index} 张截图。",
                jpeg_bytes=image.jpeg_bytes,
                schema=BalanceLlmOutput,
            )
            platform = _platform_key(parsed.platform or "")
            if platform in {"", "未知", "不确定", "无法确定", "unknown", "none", "null"} or not parsed.value:
                return self._needs_input(request_id, f"第 {index} 张截图无法确认平台或当前余额", index)
            if parsed.value_meaning not in {
                "cash_balance", "total_assets", "net_assets", "available_balance"
            }:
                return self._needs_input(request_id, f"第 {index} 张截图未显示可确认的账户余额或总资产", index)
            try:
                value = parse_decimal_amount(parsed.value)
            except (ValueError, InvalidOperation):
                return self._needs_input(request_id, f"第 {index} 张截图的金额无效", index)
            if not value.is_finite() or value < 0:
                return self._needs_input(request_id, f"第 {index} 张截图的金额无效", index)
            if platform in seen_platforms:
                return self._needs_input(
                    request_id, f"第 {index} 张截图与前面属于同一平台；每个平台只上传一张总余额截图", index
                )
            seen_platforms.add(platform)
            extracted.append((_Entry(index, image, platform, value), parsed))

        # One upload batch represents exactly one ez account. A screenshot that
        # cannot name it may use the account unambiguously identified by peers.
        kinds: set[str] = set()
        resolved_accounts: dict[str, AccountCandidate] = {}
        for entry, parsed in extracted:
            hinted = match_account(catalog, parsed.account) if parsed.account else None
            is_exchange = entry.platform in {"币安", "OKX"}
            kind = "investment" if is_exchange else self.resolve_adjust_kind(
                parsed, hinted.item if hinted else None
            )
            kinds.add(kind)
            if kind == "investment":
                for name in (parsed.account, parsed.platform):
                    match = match_account(catalog, name)
                    if match.item:
                        resolved_accounts[match.item.id] = match.item
                        break
        if len(kinds) != 1:
            return self._needs_input(request_id, "这批截图混有现金和投资内容，请按账户分批上传")
        kind = kinds.pop()
        if kind == "cash":
            account = find_default_cash_account(catalog, self.settings.default_cash_account_name)
            if not account:
                return self._needs_input(
                    request_id, f"找不到默认现金账户：{self.settings.default_cash_account_name}"
                )
        else:
            if not resolved_accounts:
                investment_accounts = [a for a in catalog.accounts if a.category == 7]
                if len(investment_accounts) == 1:
                    resolved_accounts[investment_accounts[0].id] = investment_accounts[0]
            if len(resolved_accounts) != 1:
                return self._needs_input(
                    request_id,
                    "这批投资截图无法唯一确定同一个 ez 账户；请让截图显示账户名称，或在 ez 中建立明确的对应账户",
                    choices=list(resolved_accounts.values()) or [a for a in catalog.accounts if a.category == 7],
                )
            account = next(iter(resolved_accounts.values()))

        group = _Group(account=account, adjust_kind=kind)
        for entry, parsed in extracted:
            currency = (parsed.currency or "").strip().upper()
            if currency != account.currency.upper():
                return self._needs_input(
                    request_id,
                    f"第 {entry.index} 张截图币种为 {currency or '未知'}，与账户 {account.path} 的 {account.currency} 不一致；请使用同币种截图，不会自动换算",
                    entry.index,
                )
            group.entries.append(entry)

        # Hold the account lock while calculating and writing the sole adjustment.
        async with AsyncExitStack() as locks:
            await locks.enter_async_context(self._lock_for(account.id))
            for group in (group,):
                account = group.account
                try:
                    fresh = await self._deps.ez.get_account(account.id)
                    current = ez_int_to_decimal(int(fresh.get("balance") or 0))
                except (ValueError, TypeError, EzApiError, httpx.HTTPError) as exc:
                    return BridgeResponse(
                        status=TaskStatus.Failed, request_id=request_id,
                        message=f"读取账户 {account.path} 余额失败：{exc}",
                    )
                group.target = sum((entry.value for entry in group.entries), Decimal("0"))
                current_display = -current if account.is_liability else current
                group.delta = group.target - current_display
                if group.delta == 0:
                    continue
                group.txn_type, group.category, group.ez_amount = self.reconciliation_delta(
                    catalog, account.is_liability, group.delta, group.adjust_kind
                )
                if not group.category:
                    label = "涨跌" if group.adjust_kind == "investment" else "修改余额"
                    direction = "收入" if group.txn_type == EzClient.TRANSACTION_TYPE_INCOME else "支出"
                    return self._needs_input(
                        request_id, f"账户 {account.path} 找不到「{label}」{direction}二级分类；本批图片尚未写入交易",
                        field="category",
                    )

            adjusted: list[str] = []
            unchanged: list[str] = []
            summaries: list[TransactionSummary] = []
            for group in (group,):
                account = group.account
                detail = group.detail()
                if group.delta == 0:
                    unchanged.append(
                        f"{account.path}：{detail}，余额一致 {format_amount_display(group.target)} {account.currency}"
                    )
                    continue
                assert group.category is not None
                session_id = EzClient.new_client_session_id(request_id[:8])
                picture_ids: list[str] = []
                for entry in group.entries:
                    upload = await self._deps.gateway.upload_picture(
                        entry.image.jpeg_bytes,
                        filename=f"balance-aggregate-{request_id[:8]}-{entry.index}.jpg",
                        client_session_id=f"{session_id}-pic-{entry.index}",
                    )
                    if not upload.ok:
                        return BridgeResponse(
                            status=TaskStatus.Failed, request_id=request_id,
                            message=f"第 {entry.index} 张截图上传 ez 失败：{upload.error}。已完成账户：{'、'.join(adjusted) or '无'}；请核对后再重试",
                        )
                    picture_ids.append(upload.id)
                now_unix = int(time.time())
                draft = TransactionDraft(
                    txn_type=group.txn_type,
                    category_id=group.category.id,
                    time_unix=now_unix,
                    utc_offset=utc_offset_minutes_for_zone(self.settings.default_timezone, now_unix),
                    source_account_id=account.id,
                    source_amount=group.ez_amount,
                    comment="合计对账调整：" + detail,
                    client_session_id=session_id,
                    picture_ids=picture_ids,
                )
                submit = await self._deps.gateway.submit(draft)
                if not submit.ok:
                    message = submit.error or "ez 写入失败"
                    if submit.status == SubmitStatus.Timeout:
                        message += "；提交状态不确定"
                    return BridgeResponse(
                        status=TaskStatus.Failed, request_id=request_id,
                        message=f"账户 {account.path} 对账写入失败：{message}。已完成账户：{'、'.join(adjusted) or '无'}；请先在 ez 核对再重试",
                    )
                kind_label = "涨跌" if group.adjust_kind == "investment" else "修改余额"
                adjusted.append(account.path)
                summaries.append(TransactionSummary(
                    id=submit.id, type="balance_adjust",
                    amount=format_amount_display(group.target), currency=account.currency,
                    account=account.path, category=group.category.path,
                    description=draft.comment,
                ))
                unchanged.append(
                    f"{account.path}（{kind_label}）：{detail}，目标 {format_amount_display(group.target)} {account.currency}，差额 {format_amount_display(group.delta)}，分类 {group.category.path}，附件 {len(picture_ids)} 张"
                )

        return BridgeResponse(
            status=TaskStatus.Adjusted if adjusted else TaskStatus.NoChange,
            request_id=request_id,
            message=("✅ 多图对账完成\n" if adjusted else "多图对账无需调整\n") + "\n".join(unchanged),
            transaction=summaries[0] if len(summaries) == 1 else None,
        )
