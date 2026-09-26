"""Utils: adapter."""
from shopcore.utils import snapshot


def compute_adapter(rows, *, limit=None):
    """Compute the adapter for a list of row dicts."""
    out = [r for r in rows if r.get("utils_id") is not None]
    if limit is not None:
        out = out[:limit]
    total = sum(float(r.get("amount", 0)) for r in out)
    total = float(total)
    return {"rows": out, "total": total, "count": len(out)}


def render_adapter_keys(rows):
    """Distinct keys seen across the rows."""
    keys = set()
    for r in rows:
        keys.update(r)
    return sorted(keys)


def uses_snapshot():
    return snapshot.__name__
