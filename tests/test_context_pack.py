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


BIG_TEST = "from pkg.mod import runs, other\nimport pytest\n\n\n" + "".join(
    f"def test_filler_{i}():\n" + f"    assert other({i}) == {i}\n" * 12 + "\n\n" for i in range(20)) + \
    "def test_runs():\n    assert runs([1, 1, 2]) == [[1, 1], [2]]\n\n\ndef test_last():\n    assert other(0) == 0\n"


def test_test_excerpt_shows_imports_named_tests_and_the_end():
    assert len(BIG_TEST) > cp.MAX_FILE_CHARS
    ex = cp.test_excerpt("pkg/tests/test_mod.py", BIG_TEST, "Give `runs` a key function")
    n = BIG_TEST.count("\n")
    assert ex.startswith("# lines 1-2\nfrom pkg.mod import runs, other\nimport pytest")
    assert "def test_runs():\n    assert runs([1, 1, 2])" in ex
    assert f"the last test, lines {n - 1}-{n}; the file ends at line {n}\ndef test_last():" in ex
    assert "test_filler_3" not in ex
    js = "import { describe, it } from 'vitest'\nimport { nextDay } from './index.js'\n\ndescribe('nextDay', () => {\n"
    body = "    expect(nextDay(1)).toBe(2)\n" * 10
    js += "".join(f"  it('case {i}', () => {{\n{body}  }})\n\n" for i in range(12))
    js += "  it('handles includeSameDay', () => {\n    expect(1).toBe(1)\n  })\n})\n"
    ex = cp.test_excerpt("src/nextDay/test.ts", js, "Add an `includeSameDay` option to `nextDay`")
    assert ex.startswith("# lines 1-2\nimport { describe, it }")
    assert "the last test" in ex and "it('handles includeSameDay'" in ex and "case 3" not in ex


def test_pack_puts_the_targets_test_before_its_imports():
    deps = {"a.py": {"d1.py", "d2.py"}}
    files = {"a.py", "d1.py", "d2.py", "tests/test_a.py", "tests/test_a_more.py"}
    tests = lambda p: ["tests/test_a.py", "tests/test_a_more.py"]  # noqa: E731
    got = cp.select([], ["a.py"], deps, tests, files, tests_first=True)
    assert got == ["a.py", "tests/test_a.py", "d1.py", "d2.py", "tests/test_a_more.py"]
    assert cp.select([], ["a.py"], deps, tests, files) == ["a.py", "d1.py", "d2.py", "tests/test_a.py",
                                                           "tests/test_a_more.py"]


def test_usage_block_and_extra_blocks_in_the_pack():
    entries = [{"name": "nextDay", "definitions": [{"path": "src/nextDay/index.ts", "line": 30}],
                "files": [{"path": "src/nextDay/index.ts", "lines": [1, 30]},
                          {"path": "src/index.ts", "lines": [180], "snippet": 'export * from "./nextDay/index.js";'}],
                "other": ["CHANGELOG.md"], "folder": ("src/nextDay", ["index.ts", "test.ts"])},
               {"name": "nextWeekday", "definitions": [], "files": [], "other": []}]
    block = cp.usage_block(entries)
    assert "`nextDay`, defined at src/nextDay/index.ts:30, appears in:" in block
    assert '  src/index.ts: 180    export * from "./nextDay/index.js";' in block
    assert "src/nextDay/ contains: index.ts, test.ts" in block and "non-code file(s): CHANGELOG.md" in block
    assert "`nextWeekday`: no file in the repository mentions it." in block
    keys: list[str] = []
    text = cp.build(Path("."), ["a.py"], [], lambda p: None, 2500, extra=[block], shown_keys=keys, note=cp.LEAN_NOTE)
    assert block in text and cp.LEAN_NOTE in text and len(keys) == 1
    assert cp.build(Path("."), ["a.py"], [], lambda p: None, 2500, extra=[block], skip=set(keys)) is None


def test_edit_region_numbers_the_changed_lines_with_context():
    text = "".join(f"line {i}\n" for i in range(1, 41))
    text = text.replace("line 20\n", "line 20\nNEW a\nNEW b\n")
    out = cp.edit_region("pkg/m.py", text, ["NEW a\nNEW b\n"])
    assert out.splitlines() == ["[Arbiter] pkg/m.py after this edit (42 lines):", "18| line 18", "19| line 19",
                                "20| line 20", "21| NEW a", "22| NEW b", "23| line 21", "24| line 22", "25| line 23"]
    long = "".join(f"n{i}\n" for i in range(20))
    out = cp.edit_region("m.py", "top\n" + long + "end\n", [long])
    assert "  ... (lines 5-18 as written)" in out and "| n10" not in out and "| top" in out and "| end" in out
    assert cp.edit_region("m.py", "a\n", ["missing\n"]) is None


def test_new_text_blocks_from_pi_edits_and_patches():
    from arbiter_agent.retrieval.service import new_text_blocks

    pi = {"path": "a.py", "edits": [{"oldText": "x", "newText": "y = 1\n"}, {"oldText": "z", "newText": ""}]}
    assert new_text_blocks(pi, "a.py", "a.py") == ["y = 1\n"]
    assert new_text_blocks({"file_path": "a.py", "old_string": "x", "new_string": "q"}, "a.py", "a.py") == ["q"]
    patch = ("*** Begin Patch\n*** Update File: src/a.ts\n@@ export function f() {\n   const a = 1\n-  return a\n"
             "+  return a + 1\n }\n@@\n-old\n+new\n*** Update File: src/b.ts\n@@\n+other\n*** End Patch\n")
    assert new_text_blocks({"command": patch}, "src/a.ts", "src/a.ts") == \
        ["  const a = 1\n  return a + 1\n}", "new"]
    assert new_text_blocks(patch, "src/b.ts", "src/b.ts") == ["other"]
    assert new_text_blocks({"path": "a.py", "content": "all\n"}, "a.py", "a.py") == []
