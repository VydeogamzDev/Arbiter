DISCOUNTS = {"gold": 0.05, "platinum": 0.10}


def compute_total(items, tier=None, coupon=0):
    """items: list of (unit_price, quantity). Returns the total in dollars."""
    total = sum(price * qty for price, qty in items)
    total *= 1 - DISCOUNTS.get(tier, 0.0)
    total -= coupon or 0
    return round(max(total, 0.0), 2)
