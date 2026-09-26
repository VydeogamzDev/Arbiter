import copy


def merge(base: dict, override: dict) -> dict:
    """A merged copy of two nested config dicts."""
    out = copy.deepcopy(base)
    for key, value in override.items():
        if value is None:
            out.pop(key, None)
        elif key.endswith("+") and isinstance(value, list):
            name = key[:-1]
            out[name] = list(out.get(name) or []) + copy.deepcopy(value)
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out
