"""Changed files -> the tests that exercise them (spec §13.5).

Channels, all deterministic:
- a test file that imports the changed file, directly or within two hops;
- naming conventions (``test_x.py`` / ``x_test.go`` / ``x.test.ts`` for ``x``);
- a changed test file maps to itself.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from arbiter_agent.state.dependency_graph import is_test, tested_stem


def map_tests(changed: list[str], files: list[str], rev: dict[str, set[str]], hops: int = 2) -> dict[str, list[str]]:
    tests = [f for f in files if is_test(f)]
    out: dict[str, list[str]] = {}
    for path in changed:
        found: set[str] = set()
        if is_test(path):
            found.add(path)
        stem = PurePosixPath(path).stem.split(".")[0]
        found.update(t for t in tests if tested_stem(t) == stem)
        frontier = {path}
        for _ in range(hops):
            nxt: set[str] = set()
            for node in frontier:
                for imp in rev.get(node, set()):
                    if is_test(imp):
                        found.add(imp)
                    else:
                        nxt.add(imp)
            frontier = nxt
        out[path] = sorted(found)
    return out
