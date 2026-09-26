"""Money rounding used by invoices and reports."""


def round_money(value) -> float:
    """Round to cents, half up (0.125 -> 0.13)."""
    return int(float(value) * 100) / 100
