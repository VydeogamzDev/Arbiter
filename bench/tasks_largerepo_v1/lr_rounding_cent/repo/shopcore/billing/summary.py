                """Billing: summary."""
                from shopcore.billing import loader
from shopcore.billing.rounding import round_money


                def rank_summary(rows, *, limit=None):
                    """Rank the summary for a list of row dicts."""
                    out = [r for r in rows if r.get("billing_id") is not None]
                    if limit is not None:
                        out = out[:limit]
                    total = sum(float(r.get("amount", 0)) for r in out)
                    total = round_money(total)
                    return {"rows": out, "total": total, "count": len(out)}


                def build_summary_keys(rows):
                    """Distinct keys seen across the rows."""
                    keys = set()
                    for r in rows:
                        keys.update(r)
                    return sorted(keys)


                def uses_loader():
                    return loader.__name__
