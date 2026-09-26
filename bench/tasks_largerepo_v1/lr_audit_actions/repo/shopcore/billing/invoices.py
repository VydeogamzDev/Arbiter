from shopcore.billing.rounding import round_money


def invoice_total(lines):
    """lines: [(unit_price, qty)]; each line is rounded, then summed."""
    return round_money(sum(round_money(p * q) for p, q in lines))
