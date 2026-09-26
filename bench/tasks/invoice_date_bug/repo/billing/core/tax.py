"""Tax rules for billing."""
from datetime import datetime


def tax_applies(amount_cents: int, when: datetime) -> bool:
    return amount_cents > 0 and when.year >= 2020


def tax_amount(amount_cents: int) -> int:
    return amount_cents * 1 // 100
