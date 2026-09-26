class Catalog:
    """Products with cached queries (the queries are expensive in production)."""

    def __init__(self, products):
        self._products = {p["sku"]: dict(p) for p in products}
        self._cache = {}

    def price(self, sku):
        key = ("price", sku)
        if key not in self._cache:
            self._cache[key] = self._products[sku]["price"]
        return self._cache[key]

    def category_total(self, category):
        key = ("total", category)
        if key not in self._cache:
            self._cache[key] = sum(p["price"] for p in self._products.values() if p["category"] == category)
        return self._cache[key]

    def cheapest(self, category):
        key = ("cheapest", category)
        if key not in self._cache:
            items = [p for p in self._products.values() if p["category"] == category]
            self._cache[key] = min(items, key=lambda p: p["price"])["sku"] if items else None
        return self._cache[key]

    def update_price(self, sku, new_price):
        self._products[sku]["price"] = new_price
        self._cache.clear()

    def move_category(self, sku, new_category):
        self._products[sku]["category"] = new_category
        self._cache.clear()

    def remove(self, sku):
        del self._products[sku]
        self._cache.clear()
