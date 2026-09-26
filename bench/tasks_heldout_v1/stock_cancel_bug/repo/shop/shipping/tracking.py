"""Shipping: tracking (stub)."""


def tracking_for(order) -> list:
    return [line for line in order.lines]
