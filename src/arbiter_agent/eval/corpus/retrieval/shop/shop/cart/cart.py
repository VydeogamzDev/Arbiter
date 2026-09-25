from shop.catalog.inventory import reserve_stock
from shop.pricing.totals import cart_total


class Cart:
    def __init__(self, user_id):
        self.user_id = user_id
        self.lines = []

    def add(self, session, sku, quantity=1):
        product = reserve_stock(session, sku, quantity)
        self.lines.append((product, quantity))

    def total(self, settings):
        return cart_total(self.lines, settings)
