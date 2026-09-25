"""Transitive blast radius over the file dependency graph (spec §13.3).

``changed file -> direct importers -> public/exported boundary -> mapped tests``. The graph comes
from the M6 index (``RepoIndex.graph``). Import resolution is deterministic but incomplete outside
Python (decision 0028), so blast radius only ever *raises* risk; a small radius never lowers it.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

from arbiter_agent.state.dependency_graph import is_test

WIDE_IMPORTERS = 8          # this many transitive importers makes a change "central"
MAX_DEPTH = 3
BOUNDARY_NAMES = ("__init__.py", "index.ts", "index.js", "mod.rs", "lib.rs", "main.go", "api.py")


@dataclass
class Radius:
    path: str
    direct_importers: list[str] = field(default_factory=list)
    transitive_importers: int = 0
    reaches_boundary: bool = False
    tests: list[str] = field(default_factory=list)

    @property
    def central(self) -> bool:
        return self.transitive_importers >= WIDE_IMPORTERS

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "central": self.central}


def radius(path: str, rev: dict[str, set[str]], max_depth: int = MAX_DEPTH) -> Radius:
    seen: set[str] = set()
    frontier: deque[tuple[str, int]] = deque([(path, 0)])
    while frontier:
        node, depth = frontier.popleft()
        if depth >= max_depth:
            continue
        for imp in rev.get(node, set()):
            if imp not in seen and imp != path:
                seen.add(imp)
                frontier.append((imp, depth + 1))
    non_test = {p for p in seen if not is_test(p)}
    boundary = any(p.rsplit("/", 1)[-1] in BOUNDARY_NAMES for p in non_test | {path})
    return Radius(path, sorted(p for p in rev.get(path, set()) if not is_test(p)), len(non_test), boundary,
                  sorted(p for p in seen if is_test(p)))
