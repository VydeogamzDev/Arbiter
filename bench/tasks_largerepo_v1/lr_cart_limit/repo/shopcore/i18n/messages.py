MESSAGES = {
    "cart_empty": "Your cart is empty.",
    "out_of_stock": "{sku} is out of stock.",
}


def message(key, **kw):
    return MESSAGES[key].format(**kw)
