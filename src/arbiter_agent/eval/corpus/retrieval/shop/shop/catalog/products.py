from shop.db.models import Product


def find_product(session, sku):
    rows = session.query("SELECT sku, name, price_cents, stock FROM products WHERE sku = ?", (sku,))
    return Product(*rows[0]) if rows else None


def search_products(session, text):
    like = f"%{text}%"
    return [Product(*r) for r in session.query("SELECT sku, name, price_cents, stock FROM products WHERE name LIKE ?", (like,))]
