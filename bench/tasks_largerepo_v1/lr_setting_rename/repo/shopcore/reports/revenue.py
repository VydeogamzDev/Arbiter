from shopcore.billing.invoices import invoice_total


def monthly_revenue(invoices):
    """invoices: {month: [lines]} -> {month: total}."""
    return {m: invoice_total(lines) for m, lines in sorted(invoices.items())}
