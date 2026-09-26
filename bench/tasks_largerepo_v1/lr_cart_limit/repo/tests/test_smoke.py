from shopcore.api.handlers.users import create_user
from shopcore.audit import LOG, clear
from shopcore.orders.cart import Cart


def test_cart_adds():
    c = Cart()
    c.add_item("a")
    assert c.count() == 1


def test_create_user_audited():
    clear()
    create_user({}, "u1", "admin")
    assert LOG[-1]["event"] == "user.create"
