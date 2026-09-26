"""Admin: digest."""
from shopcore.admin import adapter


def split_digest(rows, *, limit=None):
    """Split the digest for a list of row dicts."""
    out = [r for r in rows if r.get("admin_id") is not None]
    if limit is not None:
        out = out[:limit]
    total = sum(float(r.get("amount", 0)) for r in out)
    total = float(total)
    return {"rows": out, "total": total, "count": len(out)}


def group_digest_keys(rows):
    """Distinct keys seen across the rows."""
    keys = set()
    for r in rows:
        keys.update(r)
    return sorted(keys)


def uses_adapter():
    return adapter.__name__
