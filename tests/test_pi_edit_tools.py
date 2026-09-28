"""Arbiter's Pi edit tools (clients/pi/edit_core.ts), run under plain Node (type stripping)."""

import json
import shutil
import subprocess

import pytest

from arbiter_agent.clients.pi import EXTENSION

NODE = shutil.which("node")
CORE = EXTENSION.with_name("edit_core.ts")
SRC = '''class Part:
    """A partition."""

    def __len__(self):
        return 3

    @property
    def size(self):
        return (
            4
        )

    def __str__(self):
        return "p"


def helper(x):
    return x
'''


def run(tmp_path, call: str) -> tuple[int, str]:
    script = tmp_path / "t.mts"
    script.write_text(f"import * as core from {json.dumps(CORE.as_uri())};\n"
                      f"try {{ console.log(core.{call}); }} catch (e) {{ console.log('ERR ' + e.message); }}\n",
                      encoding="utf-8")
    out = subprocess.run([NODE, "--experimental-strip-types", "--no-warnings", str(script)], capture_output=True,
                         text=True, timeout=60)
    return out.returncode, (out.stdout + out.stderr).strip()


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_insert_after_block_and_line(tmp_path):
    (tmp_path / "m.py").write_bytes(SRC.replace("\n", "\r\n").encode())
    cwd = json.dumps(str(tmp_path))
    code, out = run(tmp_path, f'insertCode({cwd}, {{path: "m.py", after: "def size(self):", after_block: true, '
                              f'text: "\\n    def extra(self):\\n        return 5"}})')
    assert code == 0 and out.startswith("Inserted 3 line(s) after line 11"), out
    text = (tmp_path / "m.py").read_bytes().decode()
    assert "            4\r\n        )\r\n\r\n    def extra(self):\r\n        return 5\r\n\r\n    def __str__" in text
    code, out = run(tmp_path, f'insertCode({cwd}, {{path: "m.py", after: "return 3", text: "        # three"}})')
    assert "Inserted 1" in out and "return 3\r\n        # three\r\n" in (tmp_path / "m.py").read_bytes().decode()
    code, out = run(tmp_path, f'insertCode({cwd}, {{path: "m.py", after: "def missing():", text: "x"}})')
    assert out.startswith("ERR No line")


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_replace_def_by_name(tmp_path):
    (tmp_path / "m.py").write_text(SRC, encoding="utf-8")
    cwd = json.dumps(str(tmp_path))
    _, out = run(tmp_path, f'replaceDef({cwd}, {{path: "m.py", name: "Part.size", '
                              f'text: "    @property\\n    def size(self):\\n        return 9"}})')
    assert out.startswith("Replaced Part.size (lines 7-11)"), out
    text = (tmp_path / "m.py").read_text("utf-8")
    assert "    @property\n    def size(self):\n        return 9\n\n    def __str__" in text and "4\n" not in text
    _, out = run(tmp_path, f'replaceDef({cwd}, {{path: "m.py", name: "helper", '
                           f'text: "def helper(x):\\n    return 2*x"}})')
    assert out.startswith("Replaced helper") and text.split("def helper")[0] in (tmp_path / "m.py").read_text("utf-8")
    _, out = run(tmp_path, f'replaceDef({cwd}, {{path: "m.py", name: "Part.nope", text: "x"}})')
    assert out.startswith("ERR No def or class named")
