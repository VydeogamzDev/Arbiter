from billing.core.customers import Customer
from billing.core.money import fmt
from billing.core.timeutil import to_local


def invoice_header(customer: Customer, issued_ts: float, total_cents: int) -> dict:
    local = to_local(issued_ts, customer.utc_offset_hours)
    return {"customer": customer.name, "date": local.date().isoformat(), "total": fmt(total_cents, customer.currency)}
