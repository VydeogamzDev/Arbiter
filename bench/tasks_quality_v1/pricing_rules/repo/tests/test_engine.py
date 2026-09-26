from decimal import Decimal

from pricing.engine import price_order


def test_simple():
    r = price_order({"region": "OR", "customer": {"type": "retail"}, "shipping": "0.00",
                     "lines": [{"sku": "a", "category": "books", "unit_price": "10.00", "qty": 1}]})
    assert r["total"] == Decimal("10.00")
