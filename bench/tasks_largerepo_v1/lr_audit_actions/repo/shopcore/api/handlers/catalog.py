def delete_product(products, sku, actor):
    products.pop(sku)
    return True
