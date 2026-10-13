from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def parse_decimal_amount(text: str) -> Decimal:
    cleaned = text.strip().replace(",", "").replace("，", "")
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]
    try:
        value = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"无法解析金额: {text}") from exc
    if value.as_tuple().exponent < -2:
        raise ValueError("金额小数位不能超过 2 位")
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def decimal_to_ez_int(amount: Decimal) -> int:
    """与 ez ParseAmount 一致：整数存储，2 位小数（123.45 -> 12345）。"""
    scaled = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sign = -1 if scaled < 0 else 1
    abs_scaled = abs(scaled)
    integer_part = int(abs_scaled)
    frac = abs_scaled - Decimal(integer_part)
    decimals = int((frac * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return sign * (integer_part * 100 + decimals)


def ez_int_to_decimal(amount: int) -> Decimal:
    sign = -1 if amount < 0 else 1
    abs_amount = abs(amount)
    integer_part = abs_amount // 100
    decimals = abs_amount % 100
    return Decimal(sign) * (Decimal(integer_part) + Decimal(decimals) / Decimal(100))


def format_amount_display(amount: Decimal) -> str:
    return f"{amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):f}"
