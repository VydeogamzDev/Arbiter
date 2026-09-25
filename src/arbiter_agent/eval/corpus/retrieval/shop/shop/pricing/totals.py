from shop.pricing.discounts import apply_discounts
from shop.pricing.tax import add_tax


def cart_total(lines, settings):
    subtotal = sum(p.price_cents * q for p, q in lines)
    discounted = apply_discounts(subtotal, lines)
    return add_tax(discounted, settings["tax_rate"])
