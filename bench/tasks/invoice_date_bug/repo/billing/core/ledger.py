"""Ledger rules for billing."""
from datetime import datetime


def ledger_applies(amount_cents: int, when: datetime) -> bool:
    return amount_cents > 700 and when.year >= 2020


def ledger_amount(amount_cents: int) -> int:
    return amount_cents * 8 // 100
