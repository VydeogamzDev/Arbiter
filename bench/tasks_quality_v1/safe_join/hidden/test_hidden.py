import os
from pathlib import Path

import pytest

from files.safe import safe_join


def test_inside_ok(tmp_path):
    root = str(tmp_path)
    assert Path(safe_join(root, "a/b.txt")) == (tmp_path / "a" / "b.txt").resolve()
    assert Path(safe_join(root, "a/../b")) == (tmp_path / "b").resolve()


@pytest.mark.parametrize("p", ["../x", "a/../../x", "../../../../etc/passwd"])
def test_dotdot_rejected(tmp_path, p):
    with pytest.raises(PermissionError):
        safe_join(str(tmp_path), p)


@pytest.mark.parametrize("p", ["/etc/passwd", "C:\\Windows\\x", "c:/x", "\\\\server\\share\\x", "//server/x"])
def test_absolute_rejected(tmp_path, p):
    with pytest.raises(PermissionError):
        safe_join(str(tmp_path), p)


@pytest.mark.parametrize("p", ["%2e%2e/x", "%2e%2e%2fx", "..%2Fx", "%2E%2E%5Cx", "a/%2e%2e/%2e%2e/x"])
def test_percent_encoded(tmp_path, p):
    with pytest.raises(PermissionError):
        safe_join(str(tmp_path), p)


@pytest.mark.parametrize("p", ["..\\x", "a\\..\\..\\x"])
def test_backslash_traversal(tmp_path, p):
    with pytest.raises(PermissionError):
        safe_join(str(tmp_path), p)


@pytest.mark.parametrize("p", ["a\x00b", "a%00b"])
def test_nul_rejected(tmp_path, p):
    with pytest.raises(PermissionError):
        safe_join(str(tmp_path), p)


def test_normalized_absolute(tmp_path):
    out = safe_join(str(tmp_path), "a/./b/../c.txt")
    assert os.path.isabs(out) and ".." not in Path(out).parts and Path(out).name == "c.txt"
