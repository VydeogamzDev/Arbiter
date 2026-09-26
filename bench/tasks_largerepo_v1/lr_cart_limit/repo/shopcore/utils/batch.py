"""Utils: batch."""
from shopcore.utils import ledger


def normalize_batch(rows, *, limit=None):
    """Normalize the batch for a list of row dicts."""
    out = [r for r in rows if r.get("utils_id") is not None]
    if limit is not None:
        out = out[:limit]
    total = sum(float(r.get("amount", 0)) for r in out)
    total = float(total)
    return {"rows": out, "total": total, "count": len(out)}


def flatten_batch_keys(rows):
    """Distinct keys seen across the rows."""
    keys = set()
    for r in rows:
        keys.update(r)
    return sorted(keys)


def uses_ledger():
    return ledger.__name__
