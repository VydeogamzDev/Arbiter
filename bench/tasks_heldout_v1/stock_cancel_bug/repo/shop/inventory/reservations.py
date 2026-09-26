def reserve(stock, order) -> None:
    for sku, qty in order.lines:
        stock.reserved[sku] = stock.reserved.get(sku, 0) + qty


def release(stock, order) -> None:
    for sku, qty in order.lines:
        stock.reserved[sku] = stock.reserved.get(sku, 0) - qty
