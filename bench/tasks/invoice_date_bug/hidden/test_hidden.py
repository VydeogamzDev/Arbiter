from datetime import datetime, timezone

from billing.core.customers import Customer
from billing.invoices import invoice_header

TS = datetime(2026, 3, 1, 20, 0, tzinfo=timezone.utc).timestamp()


def test_ahead_of_utc():
    assert invoice_header(Customer(1, "A", 10), TS, 100)["date"] == "2026-03-02"


def test_behind_utc():
    early = datetime(2026, 3, 1, 3, 0, tzinfo=timezone.utc).timestamp()
    assert invoice_header(Customer(1, "A", -5), early, 100)["date"] == "2026-02-28"


def test_utc_unchanged():
    assert invoice_header(Customer(1, "A", 0), TS, 100)["date"] == "2026-03-01"
