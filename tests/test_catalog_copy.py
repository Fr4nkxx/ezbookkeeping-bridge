"""Copying a user's catalog preserves structure without copying money or transactions."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.ez.catalog_copy import copy_catalog


def _account(name: str, *, account_type: int = 1, subs=None) -> dict:
    return {
        "id": name,
        "name": name,
        "category": 8,
        "type": account_type,
        "icon": "wallet",
        "color": "123456",
        "currency": "---" if account_type == 2 else "CNY",
        "balance": "999999",
        "subAccounts": subs or [],
    }


def _category(name: str, *, id_: str, subs=None) -> dict:
    return {
        "id": id_,
        "name": name,
        "icon": "food",
        "color": "ABCDEF",
        "subCategories": subs or [],
    }


def _clients(target_accounts=None, target_categories=None):
    source = SimpleNamespace(
        list_accounts=AsyncMock(return_value=[
            _account("储蓄", account_type=2, subs=[_account("银行卡")]),
        ]),
        list_categories=AsyncMock(return_value={"2": [
            _category("日常", id_="source-parent", subs=[_category("餐饮", id_="source-child")]),
        ]}),
    )
    target = SimpleNamespace(
        list_accounts=AsyncMock(return_value=target_accounts or []),
        list_categories=AsyncMock(return_value=target_categories or {}),
        add_account=AsyncMock(return_value={"id": "new-account"}),
        add_category=AsyncMock(return_value={"id": "new-parent"}),
        delete_category=AsyncMock(return_value=True),
    )
    return source, target


def test_preview_has_no_writes() -> None:
    source, target = _clients()

    report = asyncio.run(copy_catalog(source, target))

    assert report.accounts_to_create == 2
    assert report.categories_to_create == 2
    target.add_account.assert_not_awaited()
    target.add_category.assert_not_awaited()


def test_apply_copies_structure_without_balance_and_reparents_categories() -> None:
    source, target = _clients(target_categories={"2": [_category("日常", id_="target-parent")]})

    report = asyncio.run(copy_catalog(source, target, apply=True))

    assert report.accounts_to_create == 2
    account_body = target.add_account.await_args.args[0]
    assert account_body["subAccounts"][0]["name"] == "银行卡"
    assert "balance" not in account_body
    assert "balance" not in account_body["subAccounts"][0]
    category_body = target.add_category.await_args.args[0]
    assert category_body["name"] == "餐饮"
    assert category_body["parentId"] == "target-parent"


def test_apply_creates_parent_before_child_with_new_id() -> None:
    source, target = _clients()

    asyncio.run(copy_catalog(source, target, apply=True))

    assert target.add_category.await_count == 2
    first, second = target.add_category.await_args_list
    assert first.args[0]["name"] == "日常"
    assert first.args[0]["parentId"] == "0"
    assert second.args[0]["name"] == "餐饮"
    assert second.args[0]["parentId"] == "new-parent"


def test_conflicting_account_aborts_all_writes() -> None:
    source, target = _clients(target_accounts=[_account("储蓄", account_type=1)])

    report = asyncio.run(copy_catalog(source, target, apply=True))

    assert report.conflicts
    target.add_account.assert_not_awaited()
    target.add_category.assert_not_awaited()


def test_replace_categories_deletes_target_root_before_copying() -> None:
    source, target = _clients(target_categories={"2": [
        _category("旧分类", id_="old-parent", subs=[_category("旧子分类", id_="old-child")]),
    ]})

    report = asyncio.run(copy_catalog(source, target, apply=True, replace_categories=True))

    assert report.categories_to_delete == 2
    target.delete_category.assert_awaited_once_with("old-parent")
    assert target.delete_category.await_args_list[0].args[0] == "old-parent"
    assert target.add_category.await_args_list[0].args[0]["name"] == "日常"


def test_replace_conflict_leaves_target_categories_untouched() -> None:
    source, target = _clients(
        target_accounts=[_account("储蓄", account_type=1)],
        target_categories={"2": [_category("旧分类", id_="old-parent")]},
    )

    report = asyncio.run(copy_catalog(source, target, apply=True, replace_categories=True))

    assert report.conflicts
    target.delete_category.assert_not_awaited()
