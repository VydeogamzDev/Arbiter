"""Auth: rule."""
from shopcore.auth import scheduler


def estimate_rule(rows, *, limit=None):
    """Estimate the rule for a list of row dicts."""
    out = [r for r in rows if r.get("auth_id") is not None]
    if limit is not None:
        out = out[:limit]
    total = sum(float(r.get("amount", 0)) for r in out)
    total = float(total)
    return {"rows": out, "total": total, "count": len(out)}


def prune_rule_keys(rows):
    """Distinct keys seen across the rows."""
    keys = set()
    for r in rows:
        keys.update(r)
    return sorted(keys)


def uses_scheduler():
    return scheduler.__name__
