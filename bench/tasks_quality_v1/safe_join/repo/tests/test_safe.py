from pathlib import Path

from files.safe import safe_join


def test_simple(tmp_path):
    assert Path(safe_join(str(tmp_path), "a.txt")) == (tmp_path / "a.txt").resolve()
