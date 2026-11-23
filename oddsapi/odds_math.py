from decimal import ROUND_HALF_UP, Decimal


def american_to_decimal(american: int) -> str:
    if american == 0 or -100 < american < 100:
        raise ValueError(f"invalid American price: {american}")
    a = Decimal(american)
    dec = 1 + (a / 100 if a > 0 else Decimal(100) / -a)
    return str(dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def decimal_to_american(dec: float) -> int:
    if dec <= 1:
        raise ValueError(f"invalid decimal price: {dec}")
    if dec >= 2:
        return int(Decimal(str((dec - 1) * 100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return -int(Decimal(str(100 / (dec - 1))).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def prob_to_american(p: float) -> int | None:
    """Exchange/prediction-market price (cost of a $1 contract) to American odds. None if
    the price is outside 1-99 cents, where conversion is meaningless."""
    if not 0.01 <= p <= 0.99:
        return None
    return decimal_to_american(1 / p)


def parse_american(raw) -> int:
    """Accepts ints and display strings like '+140', '-110' or '\u2212110' (U+2212 minus)."""
    if isinstance(raw, int):
        return raw
    s = str(raw).strip().replace("\u2212", "-").replace("+", "")
    if s.upper() in ("EVEN", "EV"):
        return 100
    return int(s)
