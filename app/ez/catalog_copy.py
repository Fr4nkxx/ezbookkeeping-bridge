"""Copy account structure and transaction categories between ez users.

Balances, transactions, tags, pictures, hidden flags, and display order are not copied.
"""

from dataclasses import dataclass, field
import logging
from typing import Any

from app.ez.client import EzClient

logger = logging.getLogger("uvicorn.error")


@dataclass
class CopyReport:
    accounts_to_create: int = 0
    accounts_existing: int = 0
    categories_to_create: int = 0
    categories_existing: int = 0
    categories_to_delete: int = 0
    account_names: list[str] = field(default_factory=list)
    category_names: list[str] = field(default_factory=list)
    delete_category_names: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)


def _visible(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in items if not item.get("hidden")]


def _account_count(item: dict[str, Any]) -> int:
    return 1 + sum(_account_count(child) for child in _visible(item.get("subAccounts") or []))


def _account_shape(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(item.get("name", "")),
        int(item.get("category", 0)),
        int(item.get("type", 0)),
        str(item.get("currency", "")),
        tuple(sorted(_account_shape(child) for child in _visible(item.get("subAccounts") or []))),
    )


def _account_body(item: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        key: item[key]
        for key in ("name", "category", "type", "icon", "color", "currency")
    }
    for key in ("iconType", "comment", "creditCardStatementDate", "creditCardLimit"):
        if item.get(key) is not None:
            body[key] = item[key]
    children = _visible(item.get("subAccounts") or [])
    if children:
        body["subAccounts"] = [_account_body(child) for child in children]
    # Do not send balance/balanceTime: these belong to the source user's finances.
    return body


def _category_body(item: dict[str, Any], category_type: int, parent_id: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": item["name"],
        "type": category_type,
        "parentId": parent_id,
        "icon": item["icon"],
        "color": item["color"],
    }
    for key in ("iconType", "comment"):
        if item.get(key) is not None:
            body[key] = item[key]
    return body


def _has_custom_icon(item: dict[str, Any]) -> bool:
    if int(item.get("iconType", 0)) == 1:
        return True
    children = (item.get("subAccounts") or []) + (item.get("subCategories") or [])
    return any(_has_custom_icon(child) for child in _visible(children))


async def copy_catalog(
    source: EzClient, target: EzClient, *, apply: bool = False,
    replace_categories: bool = False,
) -> CopyReport:
    """Copy visible structures; optionally replace all target categories first."""
    source_accounts = _visible(await source.list_accounts())
    target_accounts = _visible(await target.list_accounts())
    source_categories = await source.list_categories()
    target_categories = await target.list_categories()
    report = CopyReport()
    delete_roots: list[dict[str, Any]] = []
    if replace_categories:
        delete_roots = [parent for parents in target_categories.values() for parent in parents]
        report.categories_to_delete = sum(
            1 + len(parent.get("subCategories") or []) for parent in delete_roots
        )
        report.delete_category_names = [str(parent.get("name", "")) for parent in delete_roots]
        # The category plan must use the state that will exist after deletion.
        target_categories = {}

    account_actions: list[dict[str, Any]] = []
    for account in source_accounts:
        matches = [
            item for item in target_accounts
            if item.get("name") == account.get("name")
            and item.get("category") == account.get("category")
        ]
        if matches:
            if len(matches) == 1 and _account_shape(matches[0]) == _account_shape(account):
                report.accounts_existing += _account_count(account)
            else:
                report.conflicts.append(f"账户结构冲突：{account.get('name', '')}")
            continue
        if _has_custom_icon(account):
            report.conflicts.append(f"账户使用自定义图标，无法安全复制：{account.get('name', '')}")
            continue
        account_actions.append(_account_body(account))
        report.accounts_to_create += _account_count(account)
        report.account_names.append(str(account.get("name", "")))

    category_actions: list[tuple[int, dict[str, Any], dict[str, Any] | None]] = []
    for type_key, parents in source_categories.items():
        category_type = int(type_key)
        target_parents = _visible(target_categories.get(type_key, []))
        for parent in _visible(parents):
            matching_parents = [p for p in target_parents if p.get("name") == parent.get("name")]
            if len(matching_parents) > 1:
                report.conflicts.append(f"分类重名：{category_type}/{parent.get('name', '')}")
                continue
            target_parent = matching_parents[0] if matching_parents else None
            if target_parent is None and int(parent.get("iconType", 0)) == 1:
                report.conflicts.append(f"分类使用自定义图标，无法安全复制：{parent.get('name', '')}")
                continue
            if target_parent:
                report.categories_existing += 1
            else:
                report.categories_to_create += 1
                report.category_names.append(f"{category_type}/{parent.get('name', '')}")
            category_actions.append((category_type, parent, target_parent))
            target_children = _visible(target_parent.get("subCategories") or []) if target_parent else []
            for child in _visible(parent.get("subCategories") or []):
                matches = [c for c in target_children if c.get("name") == child.get("name")]
                if matches:
                    report.categories_existing += 1
                else:
                    if int(child.get("iconType", 0)) == 1:
                        report.conflicts.append(
                            f"分类使用自定义图标，无法安全复制：{parent.get('name', '')} > {child.get('name', '')}"
                        )
                    report.categories_to_create += 1
                    report.category_names.append(
                        f"{category_type}/{parent.get('name', '')} > {child.get('name', '')}"
                    )

    if not apply or report.conflicts:
        return report

    logger.info(
        "分类初始化执行：计划删除目标分类 %d 个、新增账户 %d 个、新增分类 %d 个",
        report.categories_to_delete, report.accounts_to_create, report.categories_to_create,
    )
    logger.info("分类初始化执行：开始清理目标用户的 %d 个一级分类", len(delete_roots))
    for parent in delete_roots:
        category_id = str(parent.get("id", ""))
        if not category_id:
            raise ValueError("目标分类缺少 ID，已停止清理")
        if not await target.delete_category(category_id):
            raise ValueError(f"删除目标分类 {parent.get('name', '')} 失败")
        logger.info("分类初始化执行：已删除目标一级分类 %s", parent.get("name", ""))

    logger.info("分类初始化执行：开始复制账户结构和分类")
    for body in account_actions:
        await target.add_account(body)

    for category_type, parent, target_parent in category_actions:
        if target_parent is None:
            created = await target.add_category(_category_body(parent, category_type, "0"))
            parent_id = str(created.get("id", ""))
            if not parent_id:
                raise ValueError(f"创建分类 {parent.get('name', '')} 后未返回 ID")
            target_children: list[dict[str, Any]] = []
        else:
            parent_id = str(target_parent["id"])
            target_children = _visible(target_parent.get("subCategories") or [])
        for child in _visible(parent.get("subCategories") or []):
            if not any(c.get("name") == child.get("name") for c in target_children):
                await target.add_category(_category_body(child, category_type, parent_id))
    logger.info("分类初始化执行：目标用户账户结构和分类复制完成")
    return report
