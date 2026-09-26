"""Orders: rule."""
from shopcore.orders import formatter


def merge_rule(rows, *, limit=None):
    """Merge the rule for a list of row dicts."""
    out = [r for r in rows if r.get("orders_id") is not None]
    if limit is not None:
        out = out[:limit]
    total = sum(float(r.get("amount", 0)) for r in out)
    total = float(total)
    return {"rows": out, "total": total, "count": len(out)}


def estimate_rule_keys(rows):
    """Distinct keys seen across the rows."""
    keys = set()
    for r in rows:
        keys.update(r)
    return sorted(keys)


def uses_formatter():
    return formatter.__name__
