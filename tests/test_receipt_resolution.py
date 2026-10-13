"""消费流程解析函数单元测试（不依赖 ez / LLM / HTTP）。"""

from __future__ import annotations

from decimal import Decimal

from app.core.schemas import ReceiptLlmOutput
from app.ez.catalog import AccountCandidate, CategoryCandidate, EzCatalog, TagCandidate
from app.ez.transactions import TransactionDraft
from app.pipelines.receipt import (
    Resolution,
    needs_input_message,
    resolve_amount,
    resolve_category,
    resolve_currency,
    resolve_destination_account,
    resolve_source_account,
    resolve_tags,
    resolve_time,
)


def _account(id_: str, name: str, *, currency: str = "CNY") -> AccountCandidate:
    return AccountCandidate(id=id_, name=name, path=name, currency=currency, is_liability=False, category=1)


def _category(id_: str, path: str, type_: int) -> CategoryCandidate:
    return CategoryCandidate(id=id_, name=path.split(" > ")[-1], path=path, comment="", type=type_)


def _catalog() -> EzCatalog:
    accounts = [
        _account("1", "现金账户"),
        _account("2", "招商银行储蓄卡"),
        _account("3", "招商银行信用卡"),
        _account("4", "美元账户", currency="USD"),
    ]
    categories = [
        _category("10", "日常 > 餐饮", 2),
        _category("11", "其他支出 > 待分类", 2),
        _category("20", "其他收入 > 待分类", 1),
        _category("30", "内部 > 转账", 3),
    ]
    tags = [TagCandidate(id="t1", name="出差"), TagCandidate(id="t2", name="出差补贴"), TagCandidate(id="t3", name="报销")]
    catalog = EzCatalog(accounts=accounts, categories=categories, tags=tags)
    by_name: dict[str, list[TagCandidate]] = {}
    for tag in tags:
        by_name.setdefault(tag.name, []).append(tag)
    catalog.tags_by_name = by_name
    return catalog


def _llm(**overrides) -> ReceiptLlmOutput:
    data = {"type": "expense", "amount": "12.50", "description": "午饭"}
    data.update(overrides)
    return ReceiptLlmOutput(**data)


def test_resolve_amount_ok_and_invalid() -> None:
    res = Resolution()
    assert resolve_amount(_llm(amount="12.50"), res) == Decimal("12.50")
    assert res.missing == []

    assert resolve_amount(_llm(amount="abc"), res) is None
    assert res.missing == ["amount"]


def test_source_account_defaults_to_cash_for_expense() -> None:
    res = Resolution()
    account = resolve_source_account(_catalog(), _llm(), "expense", "现金账户", res)
    assert account is not None and account.id == "1"
    assert res.missing == []


def test_source_account_ambiguous_collects_choices() -> None:
    res = Resolution()
    account = resolve_source_account(_catalog(), _llm(source_account="招商银行"), "expense", "现金账户", res)
    assert account is None
    assert res.missing == ["account"]
    assert {c.id for c in res.choices["account"]} == {"2", "3"}


def test_destination_account_missing_when_absent() -> None:
    res = Resolution()
    assert resolve_destination_account(_catalog(), _llm(type="transfer"), res) is None
    assert res.missing == ["destination_account"]


def test_category_exact_match() -> None:
    res = Resolution()
    category, note = resolve_category(_catalog(), _llm(category="日常 > 餐饮"), "expense", res)
    assert category is not None and category.id == "10"
    assert note is None


def test_category_falls_back_to_pending() -> None:
    res = Resolution()
    category, note = resolve_category(_catalog(), _llm(category="不存在的分类"), "expense", res)
    assert category is not None and category.id == "11"
    assert note is not None and "待分类" in note
    assert res.missing == []


def test_transfer_category_falls_back_to_default() -> None:
    res = Resolution()
    category, note = resolve_category(_catalog(), _llm(type="transfer", category="乱写"), "transfer", res)
    assert category is not None and category.id == "30"
    assert note == "未匹配到指定转账分类，已使用默认转账分类"


def test_tags_exact_partial_and_ambiguous() -> None:
    res = Resolution()
    assert resolve_tags(_catalog(), _llm(tags=["报销"]), res) == ["t3"]
    assert res.missing == []

    # 「出差」精确命中唯一项，不应因「出差补贴」部分匹配而歧义
    assert resolve_tags(_catalog(), _llm(tags=["出差"]), res) == ["t1"]

    res2 = Resolution()
    assert resolve_tags(_catalog(), _llm(tags=["差"]), res2) == []
    assert res2.missing == ["tags"]
    assert "tag:差" in res2.choices


def test_currency_mismatch_requires_input() -> None:
    res = Resolution()
    usd = _catalog().accounts[3]
    assert resolve_currency(_llm(currency="CNY"), usd, res) == "CNY"
    assert res.missing == ["currency"]
    assert res.choices["currency"][0].id == "USD"


def test_time_parse_and_fallback() -> None:
    unix, used_now = resolve_time(_llm(time="2025-10-13 14:30:00"), "Asia/Shanghai")
    assert used_now is False
    assert unix == 1760337000

    _unix, used_now = resolve_time(_llm(time="不是时间"), "Asia/Shanghai")
    assert used_now is True


def test_needs_input_message_uses_first_known_field() -> None:
    assert needs_input_message(["amount", "category"]) == "无法确定金额"
    assert needs_input_message(["unknown"]) == "识别信息不完整，无法记账"


def test_transaction_draft_body_truncates_comment() -> None:
    draft = TransactionDraft(
        txn_type=3,
        category_id="10",
        time_unix=1,
        utc_offset=480,
        source_account_id="1",
        source_amount=1250,
        comment="x" * 300,
        client_session_id="s",
    )
    body = draft.to_body()
    assert len(body["comment"]) == 255
    assert body["destinationAccountId"] == "0"
    assert body["sourceAmount"] == 1250
    assert "geoLocation" not in body
