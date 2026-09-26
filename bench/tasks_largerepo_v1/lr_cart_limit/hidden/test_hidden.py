import pytest

from shopcore.config import settings
from shopcore.i18n.messages import message
from shopcore.orders import errors
from shopcore.orders.cart import Cart


def fill(c, n):
    for i in range(n):
        c.add_item(f"s{i}")


def test_raises_at_limit():
    settings.reset()
    c = Cart()
    fill(c, 50)
    with pytest.raises(errors.CartLimitError) as e:
        c.add_item("one-more")
    assert "50" in str(e.value)


def test_existing_sku_ok():
    settings.reset()
    c = Cart()
    fill(c, 50)
    c.add_item("s3", 2)
    assert c.items["s3"] == 3


def test_error_hierarchy():
    assert issubclass(errors.CartLimitError, errors.OrderError)


def test_message_entry():
    assert message("cart_limit", limit=7) == "A cart can hold at most 7 different items."


def test_uses_setting():
    settings.configure(max_cart_items=2)
    try:
        c = Cart()
        fill(c, 2)
        with pytest.raises(errors.CartLimitError):
            c.add_item("x")
    finally:
        settings.reset()
