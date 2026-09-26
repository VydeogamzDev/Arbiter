"""Money rounding used by invoices and reports."""
from decimal import ROUND_HALF_UP, Decimal


def round_money(value) -> float:
    """Round to cents, half up (0.125 -> 0.13)."""
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
