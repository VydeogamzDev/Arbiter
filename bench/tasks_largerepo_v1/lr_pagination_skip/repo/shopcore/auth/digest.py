"""Auth: digest."""
from shopcore.auth import queue


def render_digest(rows, *, limit=None):
    """Render the digest for a list of row dicts."""
    out = [r for r in rows if r.get("auth_id") is not None]
    if limit is not None:
        out = out[:limit]
    total = sum(float(r.get("amount", 0)) for r in out)
    total = float(total)
    return {"rows": out, "total": total, "count": len(out)}


def estimate_digest_keys(rows):
    """Distinct keys seen across the rows."""
    keys = set()
    for r in rows:
        keys.update(r)
    return sorted(keys)


def uses_queue():
    return queue.__name__
