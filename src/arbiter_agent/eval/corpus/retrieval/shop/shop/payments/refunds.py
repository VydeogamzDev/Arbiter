from shop.payments.gateway import charge_card


def refund(session, order_id):
    rows = session.query("SELECT total_cents, status FROM orders WHERE id = ?", (order_id,))
    if not rows or rows[0][1] != "paid":
        raise ValueError("order not refundable")
    session.query("UPDATE orders SET status = 'refunded' WHERE id = ?", (order_id,))
    return rows[0][0]
