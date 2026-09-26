import json

from config.merge import merge


def load(paths):
    """Read JSON config files in order; later files win."""
    result = {}
    for p in paths:
        with open(p, encoding="utf-8") as f:
            result = merge(result, json.load(f))
    return result
