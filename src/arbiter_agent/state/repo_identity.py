"""Repository identity and path keys (spec §11.9)."""

from __future__ import annotations

import os
import subprocess
import unicodedata
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

GIT_TIMEOUT_S = 5.0


def git(cwd: str | Path, *args: str, timeout: float = GIT_TIMEOUT_S) -> str | None:
    """Run a read-only git command; None if git is missing or the command fails."""
    try:
        r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.rstrip("\n") if r.returncode == 0 else None


@dataclass(frozen=True)
class RepoIdentity:
    root: str                   # canonical worktree root, or canonical cwd outside a repo
    in_repo: bool
    common_dir: str | None = None
    head: str | None = None
    ref: str | None = None
    case_insensitive: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def key(self) -> str:
        return f"{self.common_dir or self.root}|{self.root}"


def _canon(p: str | Path) -> str:
    s = str(p)
    if s.startswith("\\?\\"):
        s = s[4:]
    try:
        s = str(Path(s).resolve())
    except OSError:
        s = os.path.abspath(s)
    if len(s) >= 2 and s[1] == ":":
        s = s[0].upper() + s[1:]
    return unicodedata.normalize("NFC", s)


@lru_cache(maxsize=64)
def volume_case_insensitive(root: str) -> bool:
    """Detect case sensitivity per volume by probing the directory itself."""
    p = Path(root)
    swapped = str(p).swapcase()
    if swapped == str(p):
        return os.name == "nt"
    try:
        return os.path.samefile(str(p), swapped)
    except OSError:
        return False


def identify(cwd: str | Path) -> RepoIdentity:
    top = git(cwd, "rev-parse", "--show-toplevel")
    if not top:
        c = _canon(cwd)
        return RepoIdentity(root=c, in_repo=False, case_insensitive=volume_case_insensitive(c))
    root = _canon(top)
    common = git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    head = git(cwd, "rev-parse", "HEAD")
    ref = git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    return RepoIdentity(root=root, in_repo=True, common_dir=_canon(common) if common else None, head=head, ref=ref,
                        case_insensitive=volume_case_insensitive(root))


def path_key(path: str | Path, ident: RepoIdentity, base: str | Path | None = None) -> str:
    """Repository-relative, '/'-separated, NFC, case-folded only on case-insensitive volumes.
    Paths outside the repository keep their canonical absolute form (never merged)."""
    p = Path(path)
    if not p.is_absolute():
        p = Path(base or ident.root) / p
    c = _canon(p)
    root = ident.root
    cmp_c, cmp_root = (c.casefold(), root.casefold()) if ident.case_insensitive else (c, root)
    if cmp_c == cmp_root or cmp_c.startswith(cmp_root.rstrip(r"\/") + os.sep):
        rel = c[len(root):].lstrip(r"\/").replace("\\", "/")
        return rel.casefold() if ident.case_insensitive else rel
    out = c.replace("\\", "/")
    return out.casefold() if ident.case_insensitive else out
