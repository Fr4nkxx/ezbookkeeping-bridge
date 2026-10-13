import asyncio
import time
from decimal import Decimal

import httpx

from app.core.errors import EzApiError
from app.core.schemas import (
    BalanceLlmOutput,
    BridgeResponse,
    TaskStatus,
    TransactionSummary,
)
from app.ez.catalog import AccountCandidate, CategoryCandidate, EzCatalog
from app.ez.client import EzClient
from app.ez.matching import find_balance_adjust_category, find_default_cash_account, match_account
from app.ez.transactions import SubmitStatus, TransactionDraft
from app.image.intake import ImagePayload
from app.pipelines.base import PipelineDeps, VisionPipeline, to_choices
from app.utils.amount import (
    decimal_to_ez_int,
    ez_int_to_decimal,
    format_amount_display,
    parse_decimal_amount,
)
from app.utils.time_util import utc_offset_minutes_for_zone

BALANCE_COMMENT = "对账调整"
_CATEGORY_TYPE_INCOME = 1
_CATEGORY_TYPE_EXPENSE = 2
_EZ_ACCOUNT_CATEGORY_INVESTMENT = 7
_CASH_CATEGORY_KEYWORD = "修改余额"
_INVESTMENT_CATEGORY_KEYWORD = "涨跌"
_INVESTMENT_NAME_HINTS = ("黄金", "股票", "基金", "虚拟币", "加密", "比特币", "以太", "证券", "持仓")


class BalancePipeline(VisionPipeline[BalanceLlmOutput]):
    """余额截图对账：按账面与截图差额记收入/支出。同一账户串行处理。"""

    kind = "balance"
    prompt_file = "balance.txt"
    instruction = "请根据图片输出 JSON。"
    output_schema = BalanceLlmOutput

    def __init__(self, deps: PipelineDeps, account_locks: dict[str, asyncio.Lock] | None = None) -> None:
        super().__init__(deps)
        self._account_locks = account_locks if account_locks is not None else {}

    def _lock_for(self, account_id: str) -> asyncio.Lock:
        if account_id not in self._account_locks:
            self._account_locks[account_id] = asyncio.Lock()
        return self._account_locks[account_id]

    def failure_message(self, exc: Exception) -> str:
        return f"余额识别失败：{exc}"

    async def resolve(
        self,
        request_id: str,
        parsed: BalanceLlmOutput,
        catalog: EzCatalog,
        image: ImagePayload,
        geo_location: tuple[float, float] | None = None,
    ) -> BridgeResponse:
        llm = parsed
        missing: list[str] = []
        choices = {}

        target = Decimal("0")
        if not llm.value:
            missing.append("amount")
        else:
            try:
                target = parse_decimal_amount(llm.value)
            except ValueError:
                missing.append("amount")

        hinted_account: AccountCandidate | None = None
        if llm.account:
            hinted = match_account(catalog, llm.account)
            if hinted.item:
                hinted_account = hinted.item  # type: ignore[assignment]

        adjust_kind = self.resolve_adjust_kind(llm, hinted_account)
        account: AccountCandidate | None = None
        if adjust_kind == "cash":
            account = find_default_cash_account(catalog, self.settings.default_cash_account_name)
            if not account:
                missing.append("account")
        elif hinted_account:
            account = hinted_account
        elif llm.account:
            match = match_account(catalog, llm.account)
            missing.append("account")
            if match.ambiguous:
                choices["account"] = to_choices(match.ambiguous)
        else:
            missing.append("account")

        currency = llm.currency or (account.currency if account else None)
        if account and currency and account.currency != currency:
            missing.append("currency")

        if missing:
            return BridgeResponse(
                status=TaskStatus.NeedsInput,
                request_id=request_id,
                message="无法完成对账，信息不足",
                missing_fields=missing,
                choices=choices or None,
                recognition={
                    **llm.model_dump(),
                    "adjust_kind": adjust_kind,
                    "target_amount": format_amount_display(target),
                    "currency": currency,
                    "account": account.path if account else None,
                },
            )

        assert account is not None

        async with self._lock_for(account.id):
            try:
                fresh = await self._deps.ez.get_account(account.id)
            except (EzApiError, httpx.HTTPError) as exc:
                return BridgeResponse(
                    status=TaskStatus.Failed,
                    request_id=request_id,
                    message=f"读取账户 {account.path} 当前余额失败：{exc}",
                )
            # ez 账户余额与交易金额相同：int64 两位小数定点（49560.75 → 4956075）
            try:
                current = ez_int_to_decimal(int(fresh.get("balance") or 0))
            except (TypeError, ValueError) as exc:
                return BridgeResponse(
                    status=TaskStatus.Failed,
                    request_id=request_id,
                    message=f"账户 {account.path} 余额格式无效：{fresh.get('balance')!r}（{exc}）",
                )
            current_display = -current if account.is_liability else current
            delta = target - current_display
            if delta.copy_abs().compare(Decimal("0.00")) == 0:
                return BridgeResponse(
                    status=TaskStatus.NoChange,
                    request_id=request_id,
                    message=f"账户 {account.path} 余额已与截图一致，无需调整。",
                )

            txn_type, category, ez_amount = self.reconciliation_delta(
                catalog, account.is_liability, delta, adjust_kind
            )
            if not category:
                keyword = (
                    _INVESTMENT_CATEGORY_KEYWORD
                    if adjust_kind == "investment"
                    else _CASH_CATEGORY_KEYWORD
                )
                return BridgeResponse(
                    status=TaskStatus.NeedsInput,
                    request_id=request_id,
                    message=(
                        f"无法匹配对账分类（adjust_kind={adjust_kind}，需名称含「{keyword}」的"
                        f"{'收入' if txn_type == EzClient.TRANSACTION_TYPE_INCOME else '支出'}二级分类），"
                        "请在 ez 中配置或设置环境变量后重试。"
                    ),
                    missing_fields=["category"],
                    recognition={
                        **llm.model_dump(),
                        "adjust_kind": adjust_kind,
                        "account": account.path,
                    },
                )

            session_id = EzClient.new_client_session_id(request_id[:8])
            upload = await self._deps.gateway.upload_picture(
                image.jpeg_bytes,
                filename=f"balance-{request_id[:8]}.jpg",
                client_session_id=f"{session_id}-pic",
            )
            if not upload.ok:
                return BridgeResponse(
                    status=TaskStatus.Failed,
                    request_id=request_id,
                    message=(
                        f"对账截图附件上传失败：{upload.error}。"
                        "请确认 ez 已开启交易图片功能（EnableTransactionPictures）。"
                    ),
                )

            now_unix = int(time.time())
            draft = TransactionDraft(
                txn_type=txn_type,
                category_id=category.id,
                time_unix=now_unix,
                utc_offset=utc_offset_minutes_for_zone(self.settings.default_timezone, now_unix),
                source_account_id=account.id,
                source_amount=ez_amount,
                comment=BALANCE_COMMENT,
                client_session_id=session_id,
                picture_ids=[upload.id],
            )
            submit = await self._deps.gateway.submit(draft)

        if not submit.ok:
            if submit.status == SubmitStatus.Timeout:
                message = submit.error or ""
            else:
                message = f"对账写入失败：{submit.error}"
            return BridgeResponse(status=TaskStatus.Failed, request_id=request_id, message=message)

        resolved_currency = currency or account.currency
        summary = TransactionSummary(
            id=submit.id,
            type="balance_adjust",
            amount=format_amount_display(target),
            currency=resolved_currency,
            account=account.path,
            category=category.path,
            description=BALANCE_COMMENT,
        )
        kind_label = "投资涨跌" if adjust_kind == "investment" else "修改余额"
        message = (
            f"✅ 对账调整成功（{kind_label}）\n"
            f"账户：{account.path}\n"
            f"目标余额：{format_amount_display(target)} {resolved_currency}\n"
            f"差额：{format_amount_display(delta)} {resolved_currency}\n"
            f"分类：{category.path or '—'}\n"
            f"备注：{BALANCE_COMMENT}\n"
            "附件：已上传截图"
        )
        return BridgeResponse(
            status=TaskStatus.Adjusted,
            request_id=request_id,
            message=message,
            transaction=summary,
        )

    @staticmethod
    def resolve_adjust_kind(llm: BalanceLlmOutput, hinted_account: AccountCandidate | None) -> str:
        raw = (llm.adjust_kind or "").strip().lower()
        if raw in {"cash", "investment"}:
            return raw
        if hinted_account and hinted_account.category == _EZ_ACCOUNT_CATEGORY_INVESTMENT:
            return "investment"
        if hinted_account:
            name_blob = f"{hinted_account.name}{hinted_account.path}"
            if any(hint in name_blob for hint in _INVESTMENT_NAME_HINTS):
                return "investment"
        platform = (llm.platform or "").strip()
        if platform and any(hint in platform for hint in _INVESTMENT_NAME_HINTS):
            return "investment"
        return "cash"

    def reconciliation_delta(
        self,
        catalog: EzCatalog,
        is_liability: bool,
        delta: Decimal,
        adjust_kind: str,
    ) -> tuple[int, CategoryCandidate | None, int]:
        # 账面少于截图 → 收入；账面多于截图 → 支出（负债账户方向相反）
        txn_type = (
            EzClient.TRANSACTION_TYPE_INCOME
            if not is_liability
            else EzClient.TRANSACTION_TYPE_EXPENSE
        )
        amount = delta
        if amount < 0:
            txn_type = (
                EzClient.TRANSACTION_TYPE_EXPENSE
                if not is_liability
                else EzClient.TRANSACTION_TYPE_INCOME
            )
            amount = -amount
        ez_amount = decimal_to_ez_int(amount)

        if adjust_kind == "investment":
            name_contains = _INVESTMENT_CATEGORY_KEYWORD
            expense_path = self.settings.balance_investment_expense_category_path
            income_path = self.settings.balance_investment_income_category_path
        else:
            name_contains = _CASH_CATEGORY_KEYWORD
            expense_path = self.settings.balance_adjust_expense_category_path
            income_path = self.settings.balance_adjust_income_category_path

        if txn_type == EzClient.TRANSACTION_TYPE_EXPENSE:
            match = find_balance_adjust_category(
                catalog,
                category_type=_CATEGORY_TYPE_EXPENSE,
                configured_path=expense_path,
                name_contains=name_contains,
            )
        else:
            match = find_balance_adjust_category(
                catalog,
                category_type=_CATEGORY_TYPE_INCOME,
                configured_path=income_path,
                name_contains=name_contains,
            )
        return txn_type, match.item, ez_amount  # type: ignore[return-value]
