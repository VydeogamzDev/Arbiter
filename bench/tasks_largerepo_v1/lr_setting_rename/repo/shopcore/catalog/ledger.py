"""Catalog: ledger."""
from shopcore.catalog import planner


def merge_ledger(rows, *, limit=None):
    """Merge the ledger for a list of row dicts."""
    out = [r for r in rows if r.get("catalog_id") is not None]
    if limit is not None:
        out = out[:limit]
    total = sum(float(r.get("amount", 0)) for r in out)
    total = float(total)
    return {"rows": out, "total": total, "count": len(out)}


def validate_ledger_keys(rows):
    """Distinct keys seen across the rows."""
    keys = set()
    for r in rows:
        keys.update(r)
    return sorted(keys)


def uses_planner():
    return planner.__name__
