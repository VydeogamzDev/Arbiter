from shop.pricing.discounts import apply_discounts
from shop.pricing.tax import add_tax


def test_bulk_discount():
    assert apply_discounts(1000, [(None, 10)]) == 950


def test_tax():
    assert add_tax(100, 0.21) == 121
