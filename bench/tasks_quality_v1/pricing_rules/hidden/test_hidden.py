from decimal import Decimal as D

import pytest

from pricing.engine import price_order


def order(region="OR", ctype="retail", lines=(("hardware", "10.00", 1),), coupon=None, shipping="0.00",
          exempt=False):
    return {"region": region, "customer": {"type": ctype, "tax_exempt": exempt}, "coupon": coupon,
            "shipping": shipping,
            "lines": [{"sku": f"s{i}", "category": c, "unit_price": p, "qty": q}
                      for i, (c, p, q) in enumerate(lines)]}


def test_subtotal():
    r = price_order(order(lines=[("books", "10.00", 2), ("books", "5.50", 1)], shipping="10.00"))
    assert r["subtotal"] == D("25.50") and r["total"] == D("35.50")


def test_business_hardware_only():
    r = price_order(order(ctype="business", lines=[("hardware", "100.00", 1), ("books", "50.00", 1)],
                          shipping="9.00"))
    assert r["discount"] == D("10.00") and r["total"] == D("140.00")


def test_coupon_save5():
    r = price_order(order(lines=[("books", "20.00", 1)], coupon="SAVE5", shipping="5.00"))
    assert r["discount"] == D("5.00") and r["total"] == D("20.00")


def test_coupon_pct15_after_business():
    r = price_order(order(ctype="business", lines=[("hardware", "100.00", 1)], coupon="PCT15",
                          shipping="5.00"))
    assert r["discount"] == D("23.50") and r["total"] == D("81.50")


def test_unknown_coupon():
    with pytest.raises(ValueError):
        price_order(order(coupon="FREE"))


def test_discount_cap():
    r = price_order(order(ctype="business", lines=[("hardware", "10.00", 1)], coupon="SAVE5"))
    assert r["discount"] == D("3.00") and r["total"] == D("7.00")


def test_free_shipping():
    assert price_order(order(lines=[("books", "100.00", 1)], shipping="7.00"))["shipping"] == D("0.00")
    r = price_order(order(lines=[("books", "99.99", 1)], shipping="7.00"))
    assert r["shipping"] == D("7.00") and r["total"] == D("106.99")


def test_tax_rates():
    assert price_order(order("CA", lines=[("hardware", "100.00", 1)]))["tax"] == D("7.25")
    assert price_order(order("NY", lines=[("hardware", "100.00", 1)]))["tax"] == D("8.88")
    assert price_order(order("OR", lines=[("hardware", "100.00", 1)]))["tax"] == D("0.00")
    with pytest.raises(ValueError):
        price_order(order("TX"))


def test_food_and_ny_shipping():
    r = price_order(order("CA", lines=[("food", "50.00", 1), ("hardware", "20.00", 1)], shipping="10.00"))
    assert r["tax"] == D("2.18") and r["total"] == D("82.18")
    r = price_order(order("NY", lines=[("hardware", "20.00", 1)], shipping="10.00"))
    assert r["tax"] == D("1.78") and r["total"] == D("31.78")


def test_coupon_spread_for_tax():
    r = price_order(order("CA", lines=[("food", "50.00", 1), ("hardware", "50.00", 1)], coupon="PCT15",
                          shipping="5.00"))
    assert r["tax"] == D("3.44") and r["total"] == D("93.44")


def test_tax_exempt():
    r = price_order(order("CA", lines=[("hardware", "100.00", 1)], exempt=True))
    assert r["tax"] == D("0.00") and r["total"] == D("100.00")


def test_round_tax_once():
    r = price_order(order("CA", lines=[("hardware", "0.10", 1)] * 3, shipping="1.00"))
    assert r["tax"] == D("0.09") and r["total"] == D("1.39")


def test_minimum_total():
    with pytest.raises(ValueError):
        price_order(order(lines=[("books", "0.50", 1)]))
