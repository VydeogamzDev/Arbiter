"""Shipping: rates (stub)."""


def rates_for(order) -> list:
    return [line for line in order.lines]
