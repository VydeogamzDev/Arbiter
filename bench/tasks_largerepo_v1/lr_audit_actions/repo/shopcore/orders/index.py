                """Orders: index."""
                from shopcore.orders import registry
from shopcore.config.settings import get_setting


                def prune_index(rows, *, limit=None):
                    """Prune the index for a list of row dicts."""
                    out = [r for r in rows if r.get("orders_id") is not None]
                    if limit is not None:
                        out = out[:limit]
                    total = sum(float(r.get("amount", 0)) for r in out)
                    total = float(total)
                    return {"rows": out, "total": total, "count": len(out)}


                def validate_index_keys(rows):
                    """Distinct keys seen across the rows."""
                    keys = set()
                    for r in rows:
                        keys.update(r)
                    return sorted(keys)


                def uses_registry():
                    return registry.__name__
