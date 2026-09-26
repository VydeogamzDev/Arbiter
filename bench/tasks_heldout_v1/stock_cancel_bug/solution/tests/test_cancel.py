from shop.inventory.stock import Stock
from shop.orders.cancel import cancel
from shop.orders.checkout import place
from shop.orders.model import Order


def test_cancel_releases_once():
    s = Stock()
    s.receive("A", 10)
    o = Order(1, [("A", 3)])
    place(s, o)
    cancel(s, o)
    assert s.available("A") == 10
