import pathlib

import pytest

from cache import TTLCache


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_validation():
    for cap, ttl in ((0, 10), (-1, 10), (2, 0), (2, -5)):
        with pytest.raises(ValueError):
            TTLCache(cap, ttl)


def test_replace_restarts_ttl():
    clk = Clock()
    c = TTLCache(3, 10, clock=clk)
    c.put("a", 1)
    clk.t = 8
    c.put("a", 2)
    clk.t = 15
    assert c.get("a") == 2
    clk.t = 18.5
    assert c.get("a") is None


def test_get_default_and_expiry():
    clk = Clock()
    c = TTLCache(3, 10, clock=clk)
    assert c.get("nope", "d") == "d"
    c.put("a", 1)
    clk.t = 10.5
    assert c.get("a", "gone") == "gone"


def test_get_refreshes_recency():
    clk = Clock()
    c = TTLCache(2, 100, clock=clk)
    c.put("a", 1)
    c.put("b", 2)
    assert c.get("a") == 1          # b is now least recently used
    c.put("c", 3)
    assert c.get("b") is None and c.get("a") == 1 and c.get("c") == 3


def test_get_does_not_extend_ttl():
    clk = Clock()
    c = TTLCache(2, 10, clock=clk)
    c.put("a", 1)
    clk.t = 9
    assert c.get("a") == 1
    clk.t = 10.5
    assert c.get("a") is None


def test_evicts_expired_first():
    clk = Clock()
    c = TTLCache(2, 10, clock=clk)
    c.put("b", "B")                 # expires at 10
    clk.t = 5
    c.put("a", "A")                 # expires at 15
    clk.t = 6
    assert c.get("b") == "B"        # b most recently used, a least
    clk.t = 11                      # b expired, a alive
    c.put("c", "C")
    assert c.get("a") == "A" and c.get("c") == "C" and len(c) == 2


def test_len_and_contains():
    clk = Clock()
    c = TTLCache(3, 10, clock=clk)
    c.put("a", 1)
    clk.t = 5
    c.put("b", 2)
    clk.t = 12
    assert len(c) == 1 and "a" not in c and "b" in c


def test_delete():
    c = TTLCache(2, 10, clock=lambda: 0)
    c.put("a", 1)
    assert c.delete("a") is True and c.delete("a") is False and c.get("a") is None


def test_added_tests():
    text = "".join(p.read_text() for p in pathlib.Path("tests").glob("test_*.py"))
    assert text.count("def test_") >= 4
