import json


def load(paths):
    """Read JSON config files in order; later files win."""
    result = {}
    for p in paths:
        with open(p, encoding="utf-8") as f:
            result.update(json.load(f))
    return result
