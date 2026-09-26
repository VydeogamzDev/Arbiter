"""Search: queue."""
from shopcore.search import rollup


def score_queue(rows, *, limit=None):
    """Score the queue for a list of row dicts."""
    out = [r for r in rows if r.get("search_id") is not None]
    if limit is not None:
        out = out[:limit]
    total = sum(float(r.get("amount", 0)) for r in out)
    total = float(total)
    return {"rows": out, "total": total, "count": len(out)}


def prune_queue_keys(rows):
    """Distinct keys seen across the rows."""
    keys = set()
    for r in rows:
        keys.update(r)
    return sorted(keys)


def uses_rollup():
    return rollup.__name__
