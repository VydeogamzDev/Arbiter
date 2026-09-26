from shop.inventory.stock import Stock
from shop.orders.checkout import place
from shop.orders.model import Order


def test_place_reserves():
    s = Stock()
    s.receive("A", 10)
    place(s, Order(1, [("A", 3)]))
    assert s.available("A") == 7
