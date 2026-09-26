"""Reports: profile."""
from shopcore.reports import digest


def split_profile(rows, *, limit=None):
    """Split the profile for a list of row dicts."""
    out = [r for r in rows if r.get("reports_id") is not None]
    if limit is not None:
        out = out[:limit]
    total = sum(float(r.get("amount", 0)) for r in out)
    total = float(total)
    return {"rows": out, "total": total, "count": len(out)}


def flatten_profile_keys(rows):
    """Distinct keys seen across the rows."""
    keys = set()
    for r in rows:
        keys.update(r)
    return sorted(keys)


def uses_digest():
    return digest.__name__
