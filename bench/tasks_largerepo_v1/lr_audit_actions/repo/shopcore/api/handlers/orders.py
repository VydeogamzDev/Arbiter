from shopcore.api.pagination import paginate


def list_orders(orders, page=1, per_page=20):
    return {"page": page, "items": paginate(orders, page, per_page)}


def cancel_order(orders, order_id, actor):
    orders[order_id]["status"] = "cancelled"
    return orders[order_id]


def refund_order(orders, order_id, actor):
    orders[order_id]["status"] = "refunded"
    return orders[order_id]
