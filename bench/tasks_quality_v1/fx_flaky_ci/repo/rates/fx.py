_CACHE = {}


def get_rate(base, quote, table):
    """The exchange rate base->quote from `table` ({(base, quote): rate})."""
    key = base
    if key not in _CACHE:
        _CACHE[key] = table[(base, quote)]
    return _CACHE[key]
