"""A tiny to-do list."""
import json

ORDER = {"urgent": 0, "normal": 1, "low": 2}
OLD = {"high": "urgent", "medium": "normal"}


def add(items, title, priority="normal"):
    priority = OLD.get(priority, priority)
    if priority not in ORDER:
        raise ValueError(f"unknown priority {priority!r}")
    item = {"title": title, "done": False, "priority": priority}
    items.append(item)
    return item


def pending(items):
    todo = [i for i in items if not i["done"]]
    return sorted(todo, key=lambda i: ORDER[i.get("priority", "normal")])


def to_json(items):
    return json.dumps(pending(items))


def complete(items, title):
    for i in items:
        if not i["done"] and i["title"] == title:
            i["done"] = True
            return True
    return False
