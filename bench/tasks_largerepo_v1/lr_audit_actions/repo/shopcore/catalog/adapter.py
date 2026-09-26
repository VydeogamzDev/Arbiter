"""Catalog: adapter."""
from shopcore.catalog import registry


def validate_adapter(rows, *, limit=None):
    """Validate the adapter for a list of row dicts."""
    out = [r for r in rows if r.get("catalog_id") is not None]
    if limit is not None:
        out = out[:limit]
    total = sum(float(r.get("amount", 0)) for r in out)
    total = float(total)
    return {"rows": out, "total": total, "count": len(out)}


def collect_adapter_keys(rows):
    """Distinct keys seen across the rows."""
    keys = set()
    for r in rows:
        keys.update(r)
    return sorted(keys)


def uses_registry():
    return registry.__name__
