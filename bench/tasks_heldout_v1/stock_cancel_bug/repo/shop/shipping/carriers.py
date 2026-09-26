"""Shipping: carriers (stub)."""


def carriers_for(order) -> list:
    return [line for line in order.lines]
