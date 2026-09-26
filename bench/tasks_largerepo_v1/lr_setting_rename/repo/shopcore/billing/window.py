"""Billing: window."""
from shopcore.billing import rule


def group_window(rows, *, limit=None):
    """Group the window for a list of row dicts."""
    out = [r for r in rows if r.get("billing_id") is not None]
    if limit is not None:
        out = out[:limit]
    total = sum(float(r.get("amount", 0)) for r in out)
    total = float(total)
    return {"rows": out, "total": total, "count": len(out)}


def merge_window_keys(rows):
    """Distinct keys seen across the rows."""
    keys = set()
    for r in rows:
        keys.update(r)
    return sorted(keys)


def uses_rule():
    return rule.__name__
