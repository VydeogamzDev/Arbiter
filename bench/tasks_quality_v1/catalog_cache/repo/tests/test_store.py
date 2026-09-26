from catalog.store import Catalog


def test_queries():
    c = Catalog([{"sku": "a", "category": "x", "price": 5}, {"sku": "b", "category": "x", "price": 3}])
    assert c.price("a") == 5 and c.category_total("x") == 8 and c.cheapest("x") == "b"
