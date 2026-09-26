def compute_total(items):
    """items: list of (unit_price, quantity). Returns the total in dollars."""
    return round(sum(price * qty for price, qty in items), 2)
