"""Integrations: profile."""
from shopcore.integrations import adapter


def group_profile(rows, *, limit=None):
    """Group the profile for a list of row dicts."""
    out = [r for r in rows if r.get("integrations_id") is not None]
    if limit is not None:
        out = out[:limit]
    total = sum(float(r.get("amount", 0)) for r in out)
    total = float(total)
    return {"rows": out, "total": total, "count": len(out)}


def merge_profile_keys(rows):
    """Distinct keys seen across the rows."""
    keys = set()
    for r in rows:
        keys.update(r)
    return sorted(keys)


def uses_adapter():
    return adapter.__name__
