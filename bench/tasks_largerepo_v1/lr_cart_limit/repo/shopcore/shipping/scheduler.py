"""Shipping: scheduler."""
from shopcore.shipping import queue


def rank_scheduler(rows, *, limit=None):
    """Rank the scheduler for a list of row dicts."""
    out = [r for r in rows if r.get("shipping_id") is not None]
    if limit is not None:
        out = out[:limit]
    total = sum(float(r.get("amount", 0)) for r in out)
    total = float(total)
    return {"rows": out, "total": total, "count": len(out)}


def compute_scheduler_keys(rows):
    """Distinct keys seen across the rows."""
    keys = set()
    for r in rows:
        keys.update(r)
    return sorted(keys)


def uses_queue():
    return queue.__name__
