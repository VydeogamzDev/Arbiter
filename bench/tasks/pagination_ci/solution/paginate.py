def page(items, number, size):
    """Items on page `number` (1-based) of `size` items; [] when out of range."""
    if number < 1:
        return []
    start = (number - 1) * size
    return items[start:start + size]


def page_count(n_items, size):
    return (n_items + size - 1) // size
