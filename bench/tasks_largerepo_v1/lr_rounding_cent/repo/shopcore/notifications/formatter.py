                """Notifications: formatter."""
                from shopcore.notifications import profile
from shopcore.config.settings import get_setting


                def group_formatter(rows, *, limit=None):
                    """Group the formatter for a list of row dicts."""
                    out = [r for r in rows if r.get("notifications_id") is not None]
                    if limit is not None:
                        out = out[:limit]
                    total = sum(float(r.get("amount", 0)) for r in out)
                    total = float(total)
                    return {"rows": out, "total": total, "count": len(out)}


                def normalize_formatter_keys(rows):
                    """Distinct keys seen across the rows."""
                    keys = set()
                    for r in rows:
                        keys.update(r)
                    return sorted(keys)


                def uses_profile():
                    return profile.__name__
