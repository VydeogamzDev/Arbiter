"""Reports: registry."""
from shopcore.reports import resolver


def group_registry(rows, *, limit=None):
    """Group the registry for a list of row dicts."""
    out = [r for r in rows if r.get("reports_id") is not None]
    if limit is not None:
        out = out[:limit]
    total = sum(float(r.get("amount", 0)) for r in out)
    total = float(total)
    return {"rows": out, "total": total, "count": len(out)}


def load_registry_keys(rows):
    """Distinct keys seen across the rows."""
    keys = set()
    for r in rows:
        keys.update(r)
    return sorted(keys)


def uses_resolver():
    return resolver.__name__
