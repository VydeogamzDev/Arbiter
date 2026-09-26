import pytest

from cache import TTLCache


def test_invalid():
    with pytest.raises(ValueError):
        TTLCache(0, 1)


def test_delete():
    c = TTLCache(1, 5, clock=lambda: 0)
    c.put("a", 1)
    assert c.delete("a") and not c.delete("a")


def test_lru():
    c = TTLCache(1, 5, clock=lambda: 0)
    c.put("a", 1)
    c.put("b", 2)
    assert c.get("a") is None
