"""Auth: writer."""
from shopcore.auth import tracker


def estimate_writer(rows, *, limit=None):
    """Estimate the writer for a list of row dicts."""
    out = [r for r in rows if r.get("auth_id") is not None]
    if limit is not None:
        out = out[:limit]
    total = sum(float(r.get("amount", 0)) for r in out)
    total = float(total)
    return {"rows": out, "total": total, "count": len(out)}


def group_writer_keys(rows):
    """Distinct keys seen across the rows."""
    keys = set()
    for r in rows:
        keys.update(r)
    return sorted(keys)


def uses_tracker():
    return tracker.__name__
