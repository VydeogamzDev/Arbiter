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


def test_excerpt_shows_named_definitions_of_a_big_file():
    filler = "\n".join(f"def f{i}():\n    return {i}\n" for i in range(400))
    text = filler + "\ndef target(x):\n    return x + 1\n\n\nclass K:\n    def method(self):\n        return 2\n"
    ex = cp.excerpt("m.py", text, "fix `target` and K.method please")
    assert "def target(x):\n    return x + 1" in ex and "def method(self):" in ex and "def f3" not in ex
    assert cp.excerpt("m.py", text, "nothing relevant here") is None
    pack = cp.build(Path("."), ["m.py"], ["m.py"], {"m.py": text}.get, 2500, query="fix target")
    assert "(excerpt of a" in pack and "def target" in pack and "def f0" not in pack


def test_related_tests_and_scope(tmp_path):
    from arbiter_agent.completion.auto_test import AutoTester, related_tests

    for rel in ("pkg/sub/mod.py", "pkg/sub/tests/test_mod.py", "pkg/other.py", "tests/test_other.py",
                "pkg/sub/__init__.py", "pkg/sub/tests/test_sub.py", "pyproject.toml"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("", encoding="utf-8")
    got = related_tests(tmp_path, ["pkg/other.py", str(tmp_path / "pkg/sub/mod.py"), "pkg/sub/__init__.py"])
    assert got == ["pkg/sub/tests/test_sub.py", "pkg/sub/tests/test_mod.py", "tests/test_other.py"]
    assert related_tests(tmp_path, ["pkg/nothing.py", "README.md"]) == []
    t = AutoTester(build_config({"completion": {"auto_test_related_min_test_files": 2}}))
    assert t.scope(tmp_path) == "related"
    assert t.command(tmp_path, ["pkg/sub/mod.py"]).endswith("-m pytest -q pkg/sub/tests/test_mod.py")
    assert t.command(tmp_path, ["pkg/nothing.py"]) is None       # no known tests: no guess at the full suite
    assert AutoTester(build_config({})).scope(tmp_path) == "full"    # few test files: the whole suite
