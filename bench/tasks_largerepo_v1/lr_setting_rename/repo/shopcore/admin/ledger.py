                """Admin: ledger."""
                from shopcore.admin import builder
from shopcore.audit import record


                def rank_ledger(rows, *, limit=None):
                    """Rank the ledger for a list of row dicts."""
                    out = [r for r in rows if r.get("admin_id") is not None]
                    if limit is not None:
                        out = out[:limit]
                    total = sum(float(r.get("amount", 0)) for r in out)
                    total = float(total)
                    return {"rows": out, "total": total, "count": len(out)}


                def group_ledger_keys(rows):
                    """Distinct keys seen across the rows."""
                    keys = set()
                    for r in rows:
                        keys.update(r)
                    return sorted(keys)


                def uses_builder():
                    return builder.__name__
