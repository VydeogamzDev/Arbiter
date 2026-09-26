import json
import pathlib
import subprocess
import sys


def run(tmp_path, *args):
    f = tmp_path / "t.txt"
    f.write_text("a b\nc\n")
    return subprocess.run([sys.executable, "wc.py", *args, str(f)], capture_output=True, text=True).stdout, f


def test_json_output(tmp_path):
    out, _ = run(tmp_path, "--json")
    assert json.loads(out) == {"lines": 2, "words": 3, "chars": 6}


def test_default_unchanged(tmp_path):
    out, f = run(tmp_path)
    assert out.strip() == f"2 3 6 {f}"


def test_readme_documented():
    text = pathlib.Path("README.md").read_text()
    assert "--json" in text.split("## Usage", 1)[-1]


def test_changelog_entry():
    text = pathlib.Path("CHANGELOG.md").read_text()
    assert "--json" in text or "json" in text.lower().split("## 1.0.0")[0]
