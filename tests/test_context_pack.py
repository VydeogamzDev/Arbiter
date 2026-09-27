from pathlib import Path

from arbiter_agent.completion.auto_test import edit_budget
from arbiter_agent.config.loader import build_config
from arbiter_agent.retrieval import context_pack as cp

SRC = '''"""Money rounding used by invoices.

More text."""
RATE = 0.2


class Money:
    def cents(self):
        return 1


def round_money(value) -> float:
    return int(float(value) * 100) / 100
'''


def test_outline_keeps_signatures_only():
    out = cp.outline("billing/rounding.py", SRC)
    assert out.splitlines() == ['"""Money rounding used by invoices."""', "RATE = 0.2", "class Money:",
                                "    def cents(self):", "def round_money(value) -> float:"]
    assert "return" not in out
    js = cp.outline("a.ts", "export function f(x) {\n  return x;\n}\nconst y = 1;\n")
    assert js.splitlines() == ["export function f(x) {", "const y = 1;"]


def test_core_is_pins_top_two_their_imports_and_tests():
    deps = {"a.py": {"dep.py"}, "b.py": set(), "c.py": {"far.py"}}
    files = {"a.py", "b.py", "c.py", "dep.py", "far.py", "tests/test_a.py"}
    got = cp.core(["a.py", "b.py", "c.py"], [], deps, lambda p: ["tests/test_a.py"] if p == "a.py" else [], files)
    assert got == {"a.py", "b.py", "dep.py", "tests/test_a.py"}


def test_large_repo_pack_outlines_low_confidence_picks():
    files = [f"pkg/m{i}.py" for i in range(cp.MAP_ALL_MAX + 5)]
    text = {"pkg/m0.py": "def target():\n    return 1\n", "pkg/m1.py": SRC}
    pack = cp.build(Path("."), files, ["pkg/m0.py", "pkg/m1.py"], text.get, 2500, full={"pkg/m0.py"})
    assert pack.startswith(cp.WORDING) and "re-read one only after it changes" in pack
    assert "--- pkg/m0.py ---\ndef target():\n    return 1" in pack
    assert "--- pkg/m1.py (outline: signatures only; read the file for the body) ---" in pack
    assert "return int(" not in pack
    small = cp.build(Path("."), files[:3], ["pkg/m0.py", "pkg/m1.py"], text.get, 2500, full={"pkg/m0.py"})
    assert "return int(" in small and "outline" not in small       # small repos: everything in full


def test_edit_budget_per_client():
    cfg = build_config({})
    assert edit_budget(cfg, "pi") == 8.0 and edit_budget(cfg, "codex") == 3.0 and edit_budget(cfg, None) == 3.0
    cfg = build_config({"completion": {"auto_test_budget_s": 2.0, "auto_test_client_budget_s": {"codex": 4.0}}})
    assert edit_budget(cfg, "codex") == 4.0 and edit_budget(cfg, "claude_code") == 2.0
