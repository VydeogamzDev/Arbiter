class OrderError(Exception):
    """Base class for order problems."""


class CartLimitError(OrderError):
    """Too many distinct items in a cart."""
