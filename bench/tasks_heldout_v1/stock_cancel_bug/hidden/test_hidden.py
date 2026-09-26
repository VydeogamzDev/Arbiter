import pathlib

from shop.inventory.stock import Stock
from shop.orders.cancel import cancel
from shop.orders.checkout import place
from shop.orders.model import Order
from shop.reports.stock_report import report


def test_cancel_restores_exactly():
    s = Stock()
    s.receive("A", 10)
    o = Order(1, [("A", 3)])
    place(s, o)
    cancel(s, o)
    assert s.available("A") == 10 and s.reserved.get("A", 0) == 0


def test_report_consistent():
    s = Stock()
    s.receive("A", 5)
    s.receive("B", 2)
    o = Order(2, [("A", 2), ("B", 1)])
    place(s, o)
    cancel(s, o)
    assert report(s) == ["A: 5/5", "B: 2/2"]


def test_cancel_is_idempotent():
    s = Stock()
    s.receive("A", 4)
    o = Order(3, [("A", 1)])
    place(s, o)
    cancel(s, o)
    cancel(s, o)
    assert s.available("A") == 4


def test_regression_test_added():
    text = "".join(p.read_text() for p in pathlib.Path("tests").rglob("test_*.py"))
    assert "cancel" in text
