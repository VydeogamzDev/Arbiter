class Cart:
    def __init__(self):
        self.items: dict[str, int] = {}

    def add_item(self, sku, qty=1):
        self.items[sku] = self.items.get(sku, 0) + qty

    def count(self):
        return len(self.items)
