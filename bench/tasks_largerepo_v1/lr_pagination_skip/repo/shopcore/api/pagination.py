def paginate(items, page, per_page):
    """Items for a 1-based page number."""
    if page < 1 or per_page < 1:
        return []
    start = page * per_page
    return items[start:start + per_page]
