"""Arbiter's Pi apply_patch tool (clients/pi/patch_core.ts), run under plain Node (type stripping)."""

import json
import shutil
import subprocess

import pytest

from arbiter_agent.clients.pi import EXTENSION

NODE = shutil.which("node")
CORE = EXTENSION.with_name("patch_core.ts")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

SRC = "def a():\n    return 1\n\n\ndef b():\n    return 2\n"


def apply(tmp_path, patch: str) -> str:
    script = tmp_path / "t.mts"
    script.write_text(f"import * as core from {json.dumps(CORE.as_uri())};\n"
                      f"try {{ console.log(core.applyPatch({json.dumps(str(tmp_path))}, {json.dumps(patch)})); }}\n"
                      "catch (e) { console.log('ERR ' + e.message); }\n", encoding="utf-8")
    out = subprocess.run([NODE, "--experimental-strip-types", "--no-warnings", str(script)], capture_output=True,
                         text=True, timeout=60)
    return (out.stdout + out.stderr).strip()


def test_update_two_files_add_and_delete(tmp_path):
    (tmp_path / "m.py").write_text(SRC)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_m.py").write_text("from m import a\n\n\ndef test_a():\n    assert a() == 1\n")
    (tmp_path / "old.txt").write_text("bye\n")
    patch = ("*** Begin Patch\n*** Update File: m.py\n@@ def b():\n-    return 2\n+    return 3\n"
             "*** Update File: tests/test_m.py\n@@\n def test_a():\n     assert a() == 1\n+\n+\n+def test_b():\n"
             "+    assert b() == 3\n*** Add File: new.py\n+X = 1\n*** Delete File: old.txt\n*** End Patch")
    out = apply(tmp_path, patch)
    assert out == "Success. Updated the following files:\nM m.py\nM tests/test_m.py\nA new.py\nD old.txt", out
    assert (tmp_path / "m.py").read_text() == SRC.replace("return 2", "return 3")
    assert (tmp_path / "tests" / "test_m.py").read_text().endswith("def test_b():\n    assert b() == 3\n")
    assert (tmp_path / "new.py").read_text() == "X = 1\n"
    assert not (tmp_path / "old.txt").exists()


def test_all_or_nothing_and_clear_errors(tmp_path):
    (tmp_path / "m.py").write_text(SRC)
    (tmp_path / "n.py").write_text("y = 1\n")
    patch = ("*** Begin Patch\n*** Update File: n.py\n-y = 1\n+y = 2\n*** Update File: m.py\n-    return 9\n"
             "+    return 0\n*** End Patch")
    out = apply(tmp_path, patch)
    assert out.startswith("ERR m.py: these lines are not in the file") and "return 9" in out, out
    assert (tmp_path / "n.py").read_text() == "y = 1\n"          # nothing written
    assert apply(tmp_path, "*** Update File: m.py").startswith("ERR the patch must start with *** Begin Patch")
    assert "outside the working directory" in apply(tmp_path, "*** Begin Patch\n*** Add File: ../x.py\n+1\n"
                                                              "*** End Patch")


def test_crlf_whitespace_tolerance_and_header_insert(tmp_path):
    (tmp_path / "m.py").write_bytes(SRC.replace("\n", "\r\n").encode())
    patch = ("*** Begin Patch\n*** Update File: m.py\n@@ def a():\n+    '''One.'''\n@@\n-def b():   \n"
             "+def b(x=0):\n*** End Patch")
    assert apply(tmp_path, patch).startswith("Success."), apply
    got = (tmp_path / "m.py").read_bytes().decode()
    assert got == "def a():\r\n    '''One.'''\r\n    return 1\r\n\r\n\r\ndef b(x=0):\r\n    return 2\r\n", got
