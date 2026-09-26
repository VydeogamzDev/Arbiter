"""Usage rules for billing."""
from datetime import datetime


def usage_applies(amount_cents: int, when: datetime) -> bool:
    return amount_cents > 400 and when.year >= 2020


def usage_amount(amount_cents: int) -> int:
    return amount_cents * 5 // 100
