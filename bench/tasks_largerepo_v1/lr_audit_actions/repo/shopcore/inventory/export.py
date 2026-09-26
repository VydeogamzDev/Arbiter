"""Inventory: export."""
from shopcore.inventory import mapper


def flatten_export(rows, *, limit=None):
    """Flatten the export for a list of row dicts."""
    out = [r for r in rows if r.get("inventory_id") is not None]
    if limit is not None:
        out = out[:limit]
    total = sum(float(r.get("amount", 0)) for r in out)
    total = float(total)
    return {"rows": out, "total": total, "count": len(out)}


def build_export_keys(rows):
    """Distinct keys seen across the rows."""
    keys = set()
    for r in rows:
        keys.update(r)
    return sorted(keys)


def uses_mapper():
    return mapper.__name__
