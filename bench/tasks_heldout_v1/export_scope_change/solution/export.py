"""Export helpers for report records."""
import csv
import io
import json
from datetime import date


def _fmt(v):
    return v.isoformat() if isinstance(v, date) else v


def to_json(records):
    return json.dumps([{k: _fmt(v) for k, v in r.items()} for r in records])


def to_csv(records, columns):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(columns)
    for r in records:
        w.writerow(["" if r.get(c) is None else _fmt(r.get(c)) for c in columns])
    return buf.getvalue()
