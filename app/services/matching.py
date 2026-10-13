"""兼容转发：实际定义见 app.ez.matching。"""

from app.ez.matching import (
    MatchResult,
    find_balance_adjust_category,
    find_default_cash_account,
    match_account,
    match_category,
    match_tags,
)

__all__ = [
    "MatchResult",
    "find_balance_adjust_category",
    "find_default_cash_account",
    "match_account",
    "match_category",
    "match_tags",
]
