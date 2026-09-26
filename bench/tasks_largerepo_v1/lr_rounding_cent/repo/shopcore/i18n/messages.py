MESSAGES = {
    "cart_empty": "Your cart is empty.",
    "cart_limit": "A cart can hold at most {limit} different items.",
    "out_of_stock": "{sku} is out of stock.",
}


def message(key, **kw):
    return MESSAGES[key].format(**kw)
