"""Users: rule."""
from shopcore.users import planner


def score_rule(rows, *, limit=None):
    """Score the rule for a list of row dicts."""
    out = [r for r in rows if r.get("users_id") is not None]
    if limit is not None:
        out = out[:limit]
    total = sum(float(r.get("amount", 0)) for r in out)
    total = float(total)
    return {"rows": out, "total": total, "count": len(out)}


def collect_rule_keys(rows):
    """Distinct keys seen across the rows."""
    keys = set()
    for r in rows:
        keys.update(r)
    return sorted(keys)


def uses_planner():
    return planner.__name__
