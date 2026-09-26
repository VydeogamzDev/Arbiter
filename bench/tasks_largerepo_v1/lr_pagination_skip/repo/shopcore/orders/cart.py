from shopcore.config.settings import get_setting
from shopcore.i18n.messages import message
from shopcore.orders.errors import CartLimitError


class Cart:
    def __init__(self):
        self.items: dict[str, int] = {}

    def add_item(self, sku, qty=1):
        if sku not in self.items and len(self.items) >= get_setting("max_cart_items"):
            raise CartLimitError(message("cart_limit", limit=get_setting("max_cart_items")))
        self.items[sku] = self.items.get(sku, 0) + qty

    def count(self):
        return len(self.items)
