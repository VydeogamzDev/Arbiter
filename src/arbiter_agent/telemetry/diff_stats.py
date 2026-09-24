"""Working-tree diff statistics against the session baseline HEAD (read-only git)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from arbiter_agent.state.repo_identity import git


@dataclass
class DiffStats:
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0
    untracked: int = 0
    files: list[str] = field(default_factory=list)
    available: bool = True

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["files"] = self.files[:50]
        return d


def diff_stats(root: str, base: str | None) -> DiffStats:
    out = git(root, "diff", "--numstat", base or "HEAD", "--")
    if out is None:
        return DiffStats(available=False)
    st = DiffStats()
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        a, d, path = parts
        st.files_changed += 1
        st.insertions += int(a) if a.isdigit() else 0
        st.deletions += int(d) if d.isdigit() else 0
        st.files.append(path)
    untracked = git(root, "ls-files", "--others", "--exclude-standard")
    if untracked:
        names = untracked.splitlines()
        st.untracked = len(names)
        st.files.extend(names[:50])
    return st


def changed_since(root: str, base: str | None, paths: list[str]) -> list[str] | None:
    """Files under ``paths`` that differ from ``base`` (committed, staged, unstaged or untracked)."""
    if not base:
        return None
    out = git(root, "diff", "--name-only", base, "--", *paths)
    if out is None:
        return None
    names = [n for n in out.splitlines() if n]
    untracked = git(root, "ls-files", "--others", "--exclude-standard", "--", *paths)
    if untracked:
        names += [n for n in untracked.splitlines() if n]
    return sorted(set(names))
