from decimal import ROUND_HALF_UP, Decimal


def american_to_decimal(american: int) -> str:
    if american == 0 or -100 < american < 100:
        raise ValueError(f"invalid American price: {american}")
    a = Decimal(american)
    dec = 1 + (a / 100 if a > 0 else Decimal(100) / -a)
    return str(dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def parse_american(raw) -> int:
    """Accepts ints and display strings like '+140', '-110' or '\u2212110' (U+2212 minus)."""
    if isinstance(raw, int):
        return raw
    s = str(raw).strip().replace("\u2212", "-").replace("+", "")
    if s.upper() in ("EVEN", "EV"):
        return 100
    return int(s)
