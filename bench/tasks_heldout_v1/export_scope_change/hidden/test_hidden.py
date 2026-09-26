import csv
import io
import json
import re
from datetime import date

from export import to_csv, to_json

RECS = [{"name": "Ada", "joined": date(2026, 3, 7), "score": 5}, {"name": "Bo", "score": 3}]


def test_json_iso_dates():
    assert json.loads(to_json(RECS))[0]["joined"] == "2026-03-07"


def test_json_no_ddmm():
    assert not re.search(r"\d{2}/\d{2}/\d{4}", to_json(RECS))


def test_csv_header_rows():
    rows = list(csv.reader(io.StringIO(to_csv(RECS, ["name", "score"]))))
    assert rows == [["name", "score"], ["Ada", "5"], ["Bo", "3"]]


def test_csv_missing_empty():
    rows = list(csv.reader(io.StringIO(to_csv(RECS, ["name", "joined"]))))
    assert rows[2] == ["Bo", ""]


def test_csv_iso_dates():
    rows = list(csv.reader(io.StringIO(to_csv(RECS, ["joined", "name"]))))
    assert rows[1] == ["2026-03-07", "Ada"]
