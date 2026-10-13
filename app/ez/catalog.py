from dataclasses import dataclass, field
from typing import Any

from app.ez.client import EzClient


@dataclass
class AccountCandidate:
    id: str
    name: str
    path: str
    currency: str
    is_liability: bool
    category: int


@dataclass
class CategoryCandidate:
    id: str
    name: str
    path: str
    comment: str
    type: int


@dataclass
class TagCandidate:
    id: str
    name: str


@dataclass
class EzCatalog:
    accounts: list[AccountCandidate] = field(default_factory=list)
    categories: list[CategoryCandidate] = field(default_factory=list)
    tags: list[TagCandidate] = field(default_factory=list)
    accounts_by_id: dict[str, AccountCandidate] = field(default_factory=dict)
    categories_by_id: dict[str, CategoryCandidate] = field(default_factory=dict)
    tags_by_id: dict[str, TagCandidate] = field(default_factory=dict)
    tags_by_name: dict[str, list[TagCandidate]] = field(default_factory=dict)


ACCOUNT_CATEGORY_LABEL = {
    1: "现金",
    2: "支票账户",
    3: "信用卡",
    4: "虚拟账户",
    5: "债务账户",
    6: "应收款项",
    7: "投资账户",
    8: "储蓄账户",
    9: "定期存款",
}


def _flatten_accounts(accounts: list[dict[str, Any]], prefix: str = "") -> list[AccountCandidate]:
    result: list[AccountCandidate] = []
    for item in accounts:
        if item.get("hidden"):
            continue
        account_type = int(item.get("type", 0))
        name = str(item.get("name", ""))
        path = f"{prefix}{name}" if not prefix else f"{prefix} > {name}"
        if account_type == EzClient.ACCOUNT_TYPE_MULTI:
            subs = item.get("subAccounts") or []
            result.extend(_flatten_accounts(subs, path))
            continue
        if account_type != EzClient.ACCOUNT_TYPE_SINGLE:
            continue
        candidate = AccountCandidate(
            id=str(item["id"]),
            name=name,
            path=path,
            currency=str(item.get("currency", "CNY")),
            is_liability=bool(item.get("isLiability")),
            category=int(item.get("category", 0)),
        )
        result.append(candidate)
    return result


def _flatten_categories(
    categories: list[dict[str, Any]],
    *,
    parent_path: str = "",
    parent_hidden: bool = False,
    category_type: int,
) -> list[CategoryCandidate]:
    result: list[CategoryCandidate] = []
    for item in categories:
        hidden = bool(item.get("hidden"))
        if hidden or parent_hidden:
            continue
        name = str(item.get("name", ""))
        parent_id = int(item.get("parentId", 0))
        path = name if not parent_path else f"{parent_path} > {name}"
        subs = item.get("subCategories") or []
        if subs:
            result.extend(
                _flatten_categories(
                    subs,
                    parent_path=path,
                    parent_hidden=hidden,
                    category_type=category_type,
                )
            )
        elif parent_id != 0:
            result.append(
                CategoryCandidate(
                    id=str(item["id"]),
                    name=name,
                    path=path,
                    comment=str(item.get("comment") or ""),
                    type=category_type,
                )
            )
    return result


async def load_catalog(ez: EzClient) -> EzCatalog:
    raw_accounts = await ez.list_accounts(visible_only=False)
    accounts = _flatten_accounts(raw_accounts)

    raw_categories = await ez.list_categories()
    categories: list[CategoryCandidate] = []
    for type_key, items in raw_categories.items():
        try:
            category_type = int(type_key)
        except (TypeError, ValueError):
            continue
        categories.extend(_flatten_categories(items or [], category_type=category_type))

    raw_tags = await ez.list_tags()
    tags: list[TagCandidate] = []
    for item in raw_tags:
        if item.get("hidden"):
            continue
        tags.append(TagCandidate(id=str(item["id"]), name=str(item.get("name", ""))))

    catalog = EzCatalog(
        accounts=accounts,
        categories=categories,
        tags=tags,
    )
    catalog.accounts_by_id = {a.id: a for a in accounts}
    catalog.categories_by_id = {c.id: c for c in categories}
    catalog.tags_by_id = {t.id: t for t in tags}
    by_name: dict[str, list[TagCandidate]] = {}
    for tag in tags:
        by_name.setdefault(tag.name, []).append(tag)
    catalog.tags_by_name = by_name
    return catalog
