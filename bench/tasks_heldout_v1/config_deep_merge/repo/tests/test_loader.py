import json

from config.loader import load


def test_single_file(tmp_path):
    p = tmp_path / "a.json"
    p.write_text(json.dumps({"x": 1}))
    assert load([p]) == {"x": 1}
