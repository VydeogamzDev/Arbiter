import json

import pytest

from todo import add, complete, pending, to_json


def test_add_defaults():
    items = []
    it = add(items, "a")
    assert it == items[0] and it["title"] == "a" and it["done"] is False and it["priority"] == "normal"


def test_pending_order():
    items = []
    add(items, "n1")
    add(items, "u1", priority="urgent")
    add(items, "l1", priority="low")
    add(items, "u2", priority="urgent")
    add(items, "n2", priority="normal")
    assert [i["title"] for i in pending(items)] == ["u1", "u2", "n1", "n2", "l1"]


def test_old_names_mapped():
    items = []
    assert add(items, "a", priority="high")["priority"] == "urgent"
    assert add(items, "b", priority="medium")["priority"] == "normal"


@pytest.mark.parametrize("bad", ["critical", "", "URGENT"])
def test_invalid_priority(bad):
    with pytest.raises(ValueError):
        add([], "x", priority=bad)


def test_to_json_order():
    items = []
    add(items, "n")
    add(items, "u", priority="urgent")
    add(items, "done-one")
    items[-1]["done"] = True
    assert [i["title"] for i in json.loads(to_json(items))] == ["u", "n"]


def test_complete():
    items = []
    add(items, "a")
    add(items, "a")
    assert complete(items, "a") is True
    assert [i["done"] for i in items] == [True, False]


def test_complete_missing():
    items = []
    add(items, "a")
    complete(items, "a")
    assert complete(items, "a") is False and complete(items, "zzz") is False
