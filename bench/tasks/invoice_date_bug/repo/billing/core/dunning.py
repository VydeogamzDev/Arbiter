"""Dunning rules for billing."""
from datetime import datetime


def dunning_applies(amount_cents: int, when: datetime) -> bool:
    return amount_cents > 600 and when.year >= 2020


def dunning_amount(amount_cents: int) -> int:
    return amount_cents * 7 // 100
