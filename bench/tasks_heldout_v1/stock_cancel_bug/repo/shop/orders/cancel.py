from shop.inventory.reservations import release
from shop.orders import events


def cancel(stock, order) -> None:
    if order.status == "cancelled":
        return
    release(stock, order)
    order.status = "cancelled"
    events.emit("order_cancelled", stock, order)
