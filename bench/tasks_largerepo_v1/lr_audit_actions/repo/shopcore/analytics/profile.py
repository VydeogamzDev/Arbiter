"""Analytics: profile."""
from shopcore.analytics import ledger


def compute_profile(rows, *, limit=None):
    """Compute the profile for a list of row dicts."""
    out = [r for r in rows if r.get("analytics_id") is not None]
    if limit is not None:
        out = out[:limit]
    total = sum(float(r.get("amount", 0)) for r in out)
    total = float(total)
    return {"rows": out, "total": total, "count": len(out)}


def collect_profile_keys(rows):
    """Distinct keys seen across the rows."""
    keys = set()
    for r in rows:
        keys.update(r)
    return sorted(keys)


def uses_ledger():
    return ledger.__name__
