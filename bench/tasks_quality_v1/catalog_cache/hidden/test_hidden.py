import pytest

from catalog.store import Catalog


def make():
    c = Catalog([{"sku": "a", "category": "x", "price": 5}, {"sku": "b", "category": "x", "price": 3},
                 {"sku": "c", "category": "y", "price": 7}])
    c.price("a"), c.category_total("x"), c.category_total("y"), c.cheapest("x"), c.cheapest("y")  # warm
    return c


def test_price_updated():
    c = make()
    c.update_price("a", 9)
    assert c.price("a") == 9


def test_category_total_after_update():
    c = make()
    c.update_price("a", 9)
    assert c.category_total("x") == 12


def test_cheapest_after_update():
    c = make()
    c.update_price("a", 1)
    assert c.cheapest("x") == "a"


def test_move_updates_both():
    c = make()
    c.move_category("b", "y")
    assert c.category_total("x") == 5 and c.category_total("y") == 10
    assert c.cheapest("x") == "a" and c.cheapest("y") == "b"


def test_remove():
    c = make()
    c.remove("b")
    assert c.category_total("x") == 5 and c.cheapest("x") == "a"
    with pytest.raises(KeyError):
        c.price("b")
