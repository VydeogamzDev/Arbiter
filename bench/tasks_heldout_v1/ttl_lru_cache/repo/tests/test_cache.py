from cache import TTLCache


def test_put_get():
    c = TTLCache(2, 10, clock=lambda: 0)
    c.put("a", 1)
    assert c.get("a") == 1
