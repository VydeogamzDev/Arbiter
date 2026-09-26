"""Refunds rules for billing."""
from datetime import datetime


def refunds_applies(amount_cents: int, when: datetime) -> bool:
    return amount_cents > 200 and when.year >= 2020


def refunds_amount(amount_cents: int) -> int:
    return amount_cents * 3 // 100
