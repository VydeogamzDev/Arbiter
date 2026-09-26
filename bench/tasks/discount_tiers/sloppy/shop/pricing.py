DISCOUNTS = {"gold": 0.05, "platinum": 0.10}


def compute_total(items, tier=None, coupon=0):
    total = sum(price * qty for price, qty in items) - (coupon or 0)
    return round(total * (1 - DISCOUNTS.get(tier, 0.0)), 2)
