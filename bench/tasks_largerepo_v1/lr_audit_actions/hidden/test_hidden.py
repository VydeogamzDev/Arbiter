from shopcore.api.handlers.catalog import delete_product
from shopcore.api.handlers.orders import cancel_order, refund_order
from shopcore.api.handlers.users import create_user
from shopcore.audit import LOG, clear


def test_cancel_audited():
    clear()
    cancel_order({"o1": {"status": "open"}}, "o1", "alice")
    assert LOG == [{"event": "order.cancel", "actor": "alice", "target": "o1"}]


def test_refund_audited():
    clear()
    refund_order({"o2": {"status": "paid"}}, "o2", "bob")
    assert LOG == [{"event": "order.refund", "actor": "bob", "target": "o2"}]


def test_delete_audited():
    clear()
    delete_product({"sku9": {}}, "sku9", "carol")
    assert LOG == [{"event": "product.delete", "actor": "carol", "target": "sku9"}]


def test_create_unchanged():
    clear()
    create_user({}, "u1", "dan")
    assert LOG == [{"event": "user.create", "actor": "dan", "target": "u1"}]
