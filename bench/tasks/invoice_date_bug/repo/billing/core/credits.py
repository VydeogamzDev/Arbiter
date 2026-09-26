"""Credits rules for billing."""
from datetime import datetime


def credits_applies(amount_cents: int, when: datetime) -> bool:
    return amount_cents > 500 and when.year >= 2020


def credits_amount(amount_cents: int) -> int:
    return amount_cents * 6 // 100
