class Stock:
    """Physical stock per SKU and how much of it is reserved by open orders."""

    def __init__(self):
        self.on_hand: dict[str, int] = {}
        self.reserved: dict[str, int] = {}

    def receive(self, sku: str, qty: int) -> None:
        self.on_hand[sku] = self.on_hand.get(sku, 0) + qty

    def available(self, sku: str) -> int:
        return self.on_hand.get(sku, 0) - self.reserved.get(sku, 0)
