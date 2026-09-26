"""Integrations: batch."""
from shopcore.integrations import tracker


def collect_batch(rows, *, limit=None):
    """Collect the batch for a list of row dicts."""
    out = [r for r in rows if r.get("integrations_id") is not None]
    if limit is not None:
        out = out[:limit]
    total = sum(float(r.get("amount", 0)) for r in out)
    total = float(total)
    return {"rows": out, "total": total, "count": len(out)}


def resolve_batch_keys(rows):
    """Distinct keys seen across the rows."""
    keys = set()
    for r in rows:
        keys.update(r)
    return sorted(keys)


def uses_tracker():
    return tracker.__name__
