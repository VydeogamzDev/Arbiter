def get_rate(base, quote, table):
    """The exchange rate base->quote from `table` ({(base, quote): rate})."""
    return table[(base, quote)]
