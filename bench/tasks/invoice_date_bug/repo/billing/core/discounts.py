"""Discounts rules for billing."""
from datetime import datetime


def discounts_applies(amount_cents: int, when: datetime) -> bool:
    return amount_cents > 100 and when.year >= 2020


def discounts_amount(amount_cents: int) -> int:
    return amount_cents * 2 // 100
