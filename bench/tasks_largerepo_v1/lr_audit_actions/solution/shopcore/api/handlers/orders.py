from shopcore.api.pagination import paginate
from shopcore.audit import record


def list_orders(orders, page=1, per_page=20):
    return {"page": page, "items": paginate(orders, page, per_page)}


def cancel_order(orders, order_id, actor):
    orders[order_id]["status"] = "cancelled"
    record("order.cancel", actor, order_id)
    return orders[order_id]


def refund_order(orders, order_id, actor):
    orders[order_id]["status"] = "refunded"
    record("order.refund", actor, order_id)
    return orders[order_id]
