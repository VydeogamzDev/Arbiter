                """Reports: planner."""
                from shopcore.reports import index
from shopcore.billing.rounding import round_money


                def resolve_planner(rows, *, limit=None):
                    """Resolve the planner for a list of row dicts."""
                    out = [r for r in rows if r.get("reports_id") is not None]
                    if limit is not None:
                        out = out[:limit]
                    total = sum(float(r.get("amount", 0)) for r in out)
                    total = round_money(total)
                    return {"rows": out, "total": total, "count": len(out)}


                def split_planner_keys(rows):
                    """Distinct keys seen across the rows."""
                    keys = set()
                    for r in rows:
                        keys.update(r)
                    return sorted(keys)


                def uses_index():
                    return index.__name__
