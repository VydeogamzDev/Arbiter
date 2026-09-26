from shop.inventory.reservations import reserve
from shop.orders import events


def place(stock, order) -> None:
    for sku, qty in order.lines:
        if stock.available(sku) < qty:
            raise ValueError(f"not enough {sku}")
    reserve(stock, order)
    order.status = "placed"
    events.emit("order_placed", stock, order)
