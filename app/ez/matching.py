from dataclasses import dataclass

from app.ez.catalog import AccountCandidate, CategoryCandidate, EzCatalog, TagCandidate


@dataclass
class MatchResult:
    item: AccountCandidate | CategoryCandidate | TagCandidate | None
    ambiguous: list[AccountCandidate | CategoryCandidate | TagCandidate]


def _normalize_name(text: str) -> str:
    return text.strip().replace(" ", "").lower()


def match_account(catalog: EzCatalog, name_or_path: str | None) -> MatchResult:
    if not name_or_path:
        return MatchResult(None, [])
    key = _normalize_name(name_or_path)
    exact: list[AccountCandidate] = []
    for account in catalog.accounts:
        if _normalize_name(account.path) == key or _normalize_name(account.name) == key:
            exact.append(account)
    if len(exact) == 1:
        return MatchResult(exact[0], [])
    if len(exact) > 1:
        return MatchResult(None, exact)
    partial = [
        a
        for a in catalog.accounts
        if key in _normalize_name(a.path) or key in _normalize_name(a.name)
    ]
    if len(partial) == 1:
        return MatchResult(partial[0], [])
    return MatchResult(None, partial)


def match_category(catalog: EzCatalog, name_or_path: str | None, *, txn_type: int) -> MatchResult:
    if not name_or_path:
        return MatchResult(None, [])
    key = _normalize_name(name_or_path)
    pool = [c for c in catalog.categories if c.type == txn_type]
    exact = [c for c in pool if _normalize_name(c.path) == key or _normalize_name(c.name) == key]
    if len(exact) == 1:
        return MatchResult(exact[0], [])
    if len(exact) > 1:
        return MatchResult(None, exact)
    partial = [c for c in pool if key in _normalize_name(c.path) or key in _normalize_name(c.name)]
    if len(partial) == 1:
        return MatchResult(partial[0], [])
    return MatchResult(None, partial)


def match_tags(catalog: EzCatalog, names: list[str]) -> tuple[list[TagCandidate], list[str], dict[str, list[TagCandidate]]]:
    resolved: list[TagCandidate] = []
    missing: list[str] = []
    ambiguous: dict[str, list[TagCandidate]] = {}
    for name in names:
        items = catalog.tags_by_name.get(name, [])
        if not items:
            partial = [t for t in catalog.tags if _normalize_name(name) in _normalize_name(t.name)]
            items = partial
        if len(items) == 1:
            resolved.append(items[0])
        elif len(items) == 0:
            missing.append(name)
        else:
            ambiguous[name] = items
    return resolved, missing, ambiguous


def find_default_cash_account(catalog: EzCatalog, default_name: str) -> AccountCandidate | None:
    match = match_account(catalog, default_name)
    if match.item:
        return match.item  # type: ignore[return-value]
    return None


def find_balance_adjust_category(
    catalog: EzCatalog,
    *,
    category_type: int,
    configured_path: str | None,
    name_contains: str,
) -> MatchResult:
    """
    @brief 按配置路径或名称关键词匹配对账用二级分类
    @param[in] category_type ez 分类类型（1=收入，2=支出）
    @param[in] configured_path 可选完整路径覆盖
    @param[in] name_contains 未配置路径时按名称包含匹配
    """
    if configured_path:
        return match_category(catalog, configured_path, txn_type=category_type)
    candidates = [
        c
        for c in catalog.categories
        if c.type == category_type and name_contains in c.name
    ]
    if len(candidates) == 1:
        return MatchResult(candidates[0], [])
    # 路径中含关键词也算（如「投资损益 > 涨跌」）
    if not candidates:
        candidates = [
            c
            for c in catalog.categories
            if c.type == category_type and name_contains in c.path
        ]
        if len(candidates) == 1:
            return MatchResult(candidates[0], [])
    return MatchResult(None, candidates)
