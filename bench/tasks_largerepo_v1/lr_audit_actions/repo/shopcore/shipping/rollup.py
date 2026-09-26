"""Shipping: rollup."""
from shopcore.shipping import filter


def rank_rollup(rows, *, limit=None):
    """Rank the rollup for a list of row dicts."""
    out = [r for r in rows if r.get("shipping_id") is not None]
    if limit is not None:
        out = out[:limit]
    total = sum(float(r.get("amount", 0)) for r in out)
    total = float(total)
    return {"rows": out, "total": total, "count": len(out)}


def merge_rollup_keys(rows):
    """Distinct keys seen across the rows."""
    keys = set()
    for r in rows:
        keys.update(r)
    return sorted(keys)


def uses_filter():
    return filter.__name__
