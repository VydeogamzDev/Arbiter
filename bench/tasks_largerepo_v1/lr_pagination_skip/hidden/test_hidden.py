from shopcore.api.handlers.orders import list_orders
from shopcore.api.pagination import paginate

ITEMS = list(range(45))


def test_first_page():
    assert paginate(ITEMS, 1, 20) == list(range(20))


def test_last_partial_page():
    assert paginate(ITEMS, 3, 20) == list(range(40, 45))


def test_out_of_range():
    assert paginate(ITEMS, 4, 20) == [] and paginate(ITEMS, 0, 20) == []


def test_handler_first_page():
    assert list_orders(ITEMS, page=1, per_page=10)["items"] == list(range(10))
