"""Shipping: labels (stub)."""


def labels_for(order) -> list:
    return [line for line in order.lines]
