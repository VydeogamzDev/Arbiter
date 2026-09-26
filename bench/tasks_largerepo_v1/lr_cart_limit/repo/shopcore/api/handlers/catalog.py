from shopcore.audit import record


def delete_product(products, sku, actor):
    products.pop(sku)
    record("product.delete", actor, sku)
    return True
