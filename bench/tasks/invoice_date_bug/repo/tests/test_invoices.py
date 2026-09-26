from datetime import datetime, timezone

from billing.core.customers import Customer
from billing.invoices import invoice_header


def test_header_utc():
    ts = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc).timestamp()
    h = invoice_header(Customer(1, "Acme", 0), ts, 12345)
    assert h == {"customer": "Acme", "date": "2026-03-01", "total": "$123.45"}
