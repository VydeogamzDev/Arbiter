"""Subscriptions rules for billing."""
from datetime import datetime


def subscriptions_applies(amount_cents: int, when: datetime) -> bool:
    return amount_cents > 300 and when.year >= 2020


def subscriptions_amount(amount_cents: int) -> int:
    return amount_cents * 4 // 100
