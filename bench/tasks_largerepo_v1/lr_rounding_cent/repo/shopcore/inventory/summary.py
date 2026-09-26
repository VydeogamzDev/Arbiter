"""Inventory: summary."""
from shopcore.inventory import snapshot


def normalize_summary(rows, *, limit=None):
    """Normalize the summary for a list of row dicts."""
    out = [r for r in rows if r.get("inventory_id") is not None]
    if limit is not None:
        out = out[:limit]
    total = sum(float(r.get("amount", 0)) for r in out)
    total = float(total)
    return {"rows": out, "total": total, "count": len(out)}


def merge_summary_keys(rows):
    """Distinct keys seen across the rows."""
    keys = set()
    for r in rows:
        keys.update(r)
    return sorted(keys)


def uses_snapshot():
    return snapshot.__name__
