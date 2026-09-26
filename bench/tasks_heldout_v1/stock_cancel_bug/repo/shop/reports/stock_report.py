def report(stock) -> list[str]:
    """One line per SKU: `sku: available/on_hand`."""
    return [f"{sku}: {stock.available(sku)}/{qty}" for sku, qty in sorted(stock.on_hand.items())]
