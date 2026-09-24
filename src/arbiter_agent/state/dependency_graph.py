"""File-level dependency graph from indexed imports (spec §11.2, §13.3).

Resolves import strings to repository files where that's deterministic:
- Python: absolute modules (repo root, ``src/``, ``lib/``) and relative imports;
- JS/TS: relative specifiers with extension and ``index`` fallbacks (bare packages are external);
- Go: import paths under the module path declared in ``go.mod``;
- Rust: ``mod x;`` (``x.rs`` / ``x/mod.rs``) and ``crate::a::b`` paths.
Anything else is recorded as external and never guessed.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable

PY_ROOTS = ("", "src", "lib", "python")
JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".d.ts")


class Resolver:
    def __init__(self, files: Iterable[str], go_module: str | None = None) -> None:
        self.files = set(files)
        self.lower = {f.lower(): f for f in self.files}
        self.go_module = go_module

    def _has(self, rel: str) -> str | None:
        rel = posixpath.normpath(rel)
        while rel.startswith("./"):
            rel = rel[2:]
        rel = rel.lstrip("/")
        if rel in self.files:
            return rel
        return self.lower.get(rel.lower())

    def python(self, importer: str, module: str, level: int, names: list[str]) -> list[str]:
        out: list[str] = []
        if level:
            base = posixpath.dirname(importer)
            for _ in range(level - 1):
                base = posixpath.dirname(base)
            parts = [p for p in module.split(".") if p] if module else []
            cands = [posixpath.join(base, *parts)] if parts else [base]
        else:
            parts = module.split(".")
            cands = [posixpath.join(root, *parts) if root else posixpath.join(*parts) for root in PY_ROOTS]
        for c in cands:
            for tail in (".py", "/__init__.py"):
                hit = self._has(c + tail)
                if hit:
                    out.append(hit)
                    break
            for n in names:  # `from pkg import mod` where mod is a submodule
                hit = self._has(posixpath.join(c, n) + ".py")
                if hit:
                    out.append(hit)
            if out:
                break
        return sorted(set(out))

    def js(self, importer: str, spec: str) -> list[str]:
        if not spec.startswith("."):
            return []
        base = posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec))
        if self._has(base) and not base.endswith("/"):
            return [self._has(base) or base]
        stem = re.sub(r"\.(js|jsx|mjs|cjs)$", "", base)
        for ext in JS_EXTS:
            hit = self._has(stem + ext)
            if hit:
                return [hit]
        for ext in JS_EXTS:
            hit = self._has(posixpath.join(base, "index" + ext))
            if hit:
                return [hit]
        return []

    def go(self, spec: str) -> list[str]:
        if not self.go_module or not spec.startswith(self.go_module):
            return []
        d = spec[len(self.go_module):].lstrip("/")
        return sorted(f for f in self.files if posixpath.dirname(f) == d and f.endswith(".go")
                      and not f.endswith("_test.go"))

    def rust(self, importer: str, spec: str) -> list[str]:
        if "::" not in spec:   # `mod x;`
            d = posixpath.dirname(importer)
            stem = posixpath.basename(importer)[:-3]
            base = d if stem in ("mod", "lib", "main") else posixpath.join(d, stem)
            for c in (posixpath.join(base, spec + ".rs"), posixpath.join(base, spec, "mod.rs")):
                hit = self._has(c)
                if hit:
                    return [hit]
            return []
        parts = spec.split("::")
        if parts[0] != "crate":
            return []
        for n in range(len(parts), 1, -1):
            rel = posixpath.join("src", *parts[1:n])
            for c in (rel + ".rs", posixpath.join(rel, "mod.rs")):
                hit = self._has(c)
                if hit:
                    return [hit]
        return []

    def resolve(self, importer: str, lang: str, module: str, level: int = 0,
                names: list[str] | None = None) -> list[str]:
        if lang == "python":
            return self.python(importer, module, level, names or [])
        if lang in ("javascript", "typescript"):
            return self.js(importer, module)
        if lang == "go":
            return self.go(module)
        if lang == "rust":
            return self.rust(importer, module)
        return []


def go_module_of(gomod_text: str | None) -> str | None:
    if not gomod_text:
        return None
    m = re.search(r"^module\s+(\S+)", gomod_text, re.M)
    return m.group(1) if m else None


TEST_NAME = re.compile(r"(^|/)(test_([^/]+)\.py|([^/]+)_test\.(py|go)|([^/]+)\.(test|spec)\.[cm]?[jt]sx?|"
                       r"([^/]+)Tests?\.(cs|java|kt))$")


def is_test(path: str) -> bool:
    return bool(TEST_NAME.search(path)) or "/tests/" in f"/{path}" or "/__tests__/" in f"/{path}"


def tested_stem(test_path: str) -> str | None:
    """``tests/test_calc.py`` -> ``calc``; ``src/calc.test.ts`` -> ``calc``."""
    m = TEST_NAME.search(test_path)
    if not m:
        return None
    return next((g for g in (m.group(3), m.group(4), m.group(6), m.group(8)) if g), None)
