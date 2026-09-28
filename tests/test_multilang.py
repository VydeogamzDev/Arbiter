"""JS/TS and Go: targeted test runs and big-file excerpts (item 5 of the sympy follow-ups)."""

import json
from pathlib import Path

from arbiter_agent.completion.auto_test import AutoTester, is_test_file, related_tests, runner_kind
from arbiter_agent.config.loader import build_config
from arbiter_agent.retrieval import context_pack as cp

RELATED = build_config({"completion": {"auto_test_related_min_test_files": 1}})


def touch(root: Path, *rels: str, text: str = "") -> None:
    for rel in rels:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")


def test_test_file_names():
    assert is_test_file("cart.test.ts") and is_test_file("cart.spec.jsx") and is_test_file("cart_test.go")
    assert is_test_file("test_cart.py") and not is_test_file("cart.ts") and not is_test_file("testing.go")


def test_jest_and_vitest_find_related_tests_themselves(tmp_path):
    touch(tmp_path, "src/cart.ts", "src/cart.test.ts", "src/other.test.ts")
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}), encoding="utf-8")
    assert runner_kind(tmp_path, "npm test --silent") == "jest"
    t = AutoTester(RELATED)
    assert t.scope(tmp_path) == "related"
    assert t.command(tmp_path, ["src/cart.ts"]) == "npx jest --findRelatedTests src/cart.ts --passWithNoTests"
    assert t.command(tmp_path, ["README.md"]) is None
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "vitest"}}), encoding="utf-8")
    t = AutoTester(RELATED)
    assert t.command(tmp_path, [str(tmp_path / "src/cart.ts")]) == \
        "npx vitest related --run src/cart.ts --passWithNoTests"


def test_other_node_runner_gets_mapped_test_files(tmp_path):
    touch(tmp_path, "lib/cart.js", "lib/__tests__/cart.test.js", "lib/x.spec.js")
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "mocha"}}), encoding="utf-8")
    assert related_tests(tmp_path, ["lib/cart.js"]) == ["lib/__tests__/cart.test.js"]
    assert AutoTester(RELATED).command(tmp_path, ["lib/cart.js"]) == "npm test --silent -- lib/__tests__/cart.test.js"


def test_go_tests_the_changed_packages(tmp_path):
    touch(tmp_path, "go.mod", "cart/cart.go", "cart/cart_test.go", "main.go", "main_test.go")
    t = AutoTester(RELATED)
    assert runner_kind(tmp_path, t.base_command(tmp_path)) == "go"
    assert t.command(tmp_path, ["cart/cart.go", "main.go", "cart/cart_test.go"]) == "go test ./cart ."


TS = "\n".join(f"export function filler{i}(x: number): number {{\n  return x + {i};\n}}\n" for i in range(300)) + '''
export class Cart {
  private items: string[] = [];

  addItem(sku: string): void {
    if (this.items.length > 50) { throw new Error("full"); }
    this.items.push(sku);
  }

  count(): number {
    return this.items.length;
  }
}

export const maxItems = (n: number) => {
  return n * 2;
};
'''

GO = "\n".join(f"func filler{i}(x int) int {{\n\treturn x + {i}\n}}\n" for i in range(300)) + '''
type Cart struct {
\titems []string
}

func (c *Cart) AddItem(sku string) error {
\tif len(c.items) > 50 {
\t\treturn errors.New("full")
\t}
\tc.items = append(c.items, sku)
\treturn nil
}
'''


def test_braced_excerpts():
    ex = cp.excerpt("src/cart.ts", TS, "make `Cart.addItem` reject more than `maxItems`")
    assert "addItem(sku: string): void {" in ex and 'this.items.push(sku);\n  }' in ex
    assert "export const maxItems = (n: number) => {\n  return n * 2;\n};" in ex and "filler3" not in ex
    ex = cp.excerpt("cart/cart.go", GO, "AddItem should return an error when the Cart is full")
    assert "func (c *Cart) AddItem(sku string) error {" in ex and "\treturn nil\n}" in ex
    assert "type Cart struct {\n\titems []string\n}" in ex and "filler7" not in ex
    assert cp.excerpt("src/cart.ts", TS, "nothing named here") is None


def test_gate_does_not_block_when_no_test_covers_the_change():
    """Item 6: in a related-scope repo, a change to a file no test covers can't be verified by
    running tests, so the gate records the claim instead of sending the agent to find tests."""
    from arbiter_agent.eval.trace_replay import Harness

    cfg = {"completion": {"gate_mode": "block", "auto_test": "after_edit", "auto_test_related_min_test_files": 0}}
    with Harness(config=cfg) as h:
        h.write("pyproject.toml", "[tool.pytest.ini_options]\n")
        h.write("pkg/__init__.py", "")
        h.write("pkg/covered.py", "def f():\n    return 1\n")
        h.write("pkg/tests/test_covered.py", "from pkg.covered import f\n\ndef test_f():\n    assert f() == 1\n")
        h.prompt("Update pkg/notes.py with a helper")
        h.edit("pkg/notes.py", "def helper():\n    return 2\n")
        h.drain()
        assert h.stop("Done: added the helper.") == {}          # no test covers pkg/notes.py: recorded only
        h.edit("pkg/covered.py", "def f():\n    return 2\n")    # covered, and now failing: still blocked
        h.drain()
        assert h.stop("Done: changed f.").get("decision") == "block"
