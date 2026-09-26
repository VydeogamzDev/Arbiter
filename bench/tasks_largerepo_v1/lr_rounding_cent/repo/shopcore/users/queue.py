"""Users: queue."""
from shopcore.users import index


def render_queue(rows, *, limit=None):
    """Render the queue for a list of row dicts."""
    out = [r for r in rows if r.get("users_id") is not None]
    if limit is not None:
        out = out[:limit]
    total = sum(float(r.get("amount", 0)) for r in out)
    total = float(total)
    return {"rows": out, "total": total, "count": len(out)}


def collect_queue_keys(rows):
    """Distinct keys seen across the rows."""
    keys = set()
    for r in rows:
        keys.update(r)
    return sorted(keys)


def uses_index():
    return index.__name__
