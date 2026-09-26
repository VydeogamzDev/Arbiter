                """Reports: rule."""
                from shopcore.reports import profile
from shopcore.billing.rounding import round_money


                def validate_rule(rows, *, limit=None):
                    """Validate the rule for a list of row dicts."""
                    out = [r for r in rows if r.get("reports_id") is not None]
                    if limit is not None:
                        out = out[:limit]
                    total = sum(float(r.get("amount", 0)) for r in out)
                    total = round_money(total)
                    return {"rows": out, "total": total, "count": len(out)}


                def merge_rule_keys(rows):
                    """Distinct keys seen across the rows."""
                    keys = set()
                    for r in rows:
                        keys.update(r)
                    return sorted(keys)


                def uses_profile():
                    return profile.__name__
