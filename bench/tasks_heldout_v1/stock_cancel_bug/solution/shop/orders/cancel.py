from shop.orders import events


def cancel(stock, order) -> None:
    if order.status == "cancelled":
        return
    order.status = "cancelled"
    events.emit("order_cancelled", stock, order)   # the event handler releases the reservation
