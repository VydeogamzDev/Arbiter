from shop.catalog.products import find_product


class OutOfStock(Exception):
    pass


def reserve_stock(session, sku, quantity):
    product = find_product(session, sku)
    if product is None or product.stock < quantity:
        raise OutOfStock(sku)
    session.query("UPDATE products SET stock = stock - ? WHERE sku = ?", (quantity, sku))
    return product
