                """Reports: digest."""
                from shopcore.reports import reader
from shopcore.billing.rounding import round_money


                def score_digest(rows, *, limit=None):
                    """Score the digest for a list of row dicts."""
                    out = [r for r in rows if r.get("reports_id") is not None]
                    if limit is not None:
                        out = out[:limit]
                    total = sum(float(r.get("amount", 0)) for r in out)
                    total = round_money(total)
                    return {"rows": out, "total": total, "count": len(out)}


                def normalize_digest_keys(rows):
                    """Distinct keys seen across the rows."""
                    keys = set()
                    for r in rows:
                        keys.update(r)
                    return sorted(keys)


                def uses_reader():
                    return reader.__name__
