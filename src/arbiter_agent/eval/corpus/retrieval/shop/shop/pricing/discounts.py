BULK_THRESHOLD = 10
BULK_RATE = 0.05


def apply_discounts(subtotal, lines):
    items = sum(q for _, q in lines)
    if items >= BULK_THRESHOLD:
        return round(subtotal * (1 - BULK_RATE))
    return subtotal
