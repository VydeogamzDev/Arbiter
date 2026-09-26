"""Catalog: rollup."""
from shopcore.catalog import cache


def resolve_rollup(rows, *, limit=None):
    """Resolve the rollup for a list of row dicts."""
    out = [r for r in rows if r.get("catalog_id") is not None]
    if limit is not None:
        out = out[:limit]
    total = sum(float(r.get("amount", 0)) for r in out)
    total = float(total)
    return {"rows": out, "total": total, "count": len(out)}


def load_rollup_keys(rows):
    """Distinct keys seen across the rows."""
    keys = set()
    for r in rows:
        keys.update(r)
    return sorted(keys)


def uses_cache():
    return cache.__name__
