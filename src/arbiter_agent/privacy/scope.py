"""Project scope (spec §16.3.1): decide, before parsing, whether a session's project may be
ingested. Modes: ``all_except_excluded`` (default) and ``allow_list``.

Exclusion sources: config ``privacy.project_exclude`` (paths/globs), the CLI-managed
``scope.json`` (``arbiter exclude`` / ``arbiter include``), and ``.arbiterignore`` files at or
above the session's working directory."""

from __future__ import annotations

import fnmatch
import json
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

IGNORE_FILE = ".arbiterignore"


def norm(path: str | os.PathLike[str]) -> str:
    p = str(Path(path).expanduser())
    try:
        p = str(Path(p).resolve())
    except OSError:
        p = os.path.abspath(p)
    p = p.replace("\\", "/").rstrip("/")
    return p.casefold() if sys.platform == "win32" else p


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "/")


@dataclass(frozen=True)
class ScopeDecision:
    in_scope: bool
    reason: str


class ProjectScope:
    def __init__(self, scope_file: Path, mode: str = "all_except_excluded",
                 config_excludes: list[str] | None = None, respect_arbiterignore: bool = True) -> None:
        self.scope_file = scope_file
        self.mode = mode
        self.config_excludes = list(config_excludes or [])
        self.respect_arbiterignore = respect_arbiterignore
        self._lock = threading.Lock()
        self._ignore_cache: dict[str, bool] = {}

    # ---- persisted CLI lists -------------------------------------------------
    def _load(self) -> dict[str, list[str]]:
        try:
            data = json.loads(self.scope_file.read_text(encoding="utf-8"))
            return {"exclude": list(data.get("exclude", [])), "include": list(data.get("include", []))}
        except (OSError, ValueError):
            return {"exclude": [], "include": []}

    def _save(self, data: dict[str, list[str]]) -> None:
        from arbiter_agent.paths import write_private

        write_private(self.scope_file, json.dumps(data, indent=1).encode())

    def exclude(self, path: str) -> None:
        with self._lock:
            data = self._load()
            p = norm(path)
            if p not in data["exclude"]:
                data["exclude"].append(p)
            data["include"] = [x for x in data["include"] if x != p]
            self._save(data)
            self._ignore_cache.clear()

    def include(self, path: str) -> None:
        with self._lock:
            data = self._load()
            p = norm(path)
            data["exclude"] = [x for x in data["exclude"] if x != p]
            if p not in data["include"]:
                data["include"].append(p)
            self._save(data)
            self._ignore_cache.clear()

    def lists(self) -> dict[str, list[str]]:
        return self._load()

    # ---- decision ------------------------------------------------------------
    def _has_ignore_file(self, path: str) -> bool:
        if path in self._ignore_cache:
            return self._ignore_cache[path]
        found = False
        cur = Path(path)
        for d in (cur, *cur.parents):
            try:
                if (d / IGNORE_FILE).is_file():
                    found = True
                    break
            except OSError:
                break
        self._ignore_cache[path] = found
        return found

    def _matches(self, path: str, patterns: list[str]) -> bool:
        for pat in patterns:
            np = norm(pat) if not any(c in pat for c in "*?[") else pat.replace("\\", "/")
            if any(c in np for c in "*?["):
                cmp = np.casefold() if sys.platform == "win32" else np
                if fnmatch.fnmatch(path, cmp) or fnmatch.fnmatch(path + "/", cmp):
                    return True
            elif _under(path, np):
                return True
        return False

    def decide(self, cwd: str | None) -> ScopeDecision:
        if not cwd:
            # Unknown working directory: allowed only in all_except_excluded mode.
            return ScopeDecision(self.mode == "all_except_excluded", "no_cwd")
        path = norm(cwd)
        lists = self._load()
        if self._matches(path, self.config_excludes):
            return ScopeDecision(False, "config_exclude")
        if self._matches(path, lists["exclude"]):
            return ScopeDecision(False, "cli_exclude")
        if self.respect_arbiterignore and self._has_ignore_file(path):
            return ScopeDecision(False, "arbiterignore")
        if self.mode == "allow_list":
            ok = self._matches(path, lists["include"])
            return ScopeDecision(ok, "allow_list" if ok else "not_in_allow_list")
        return ScopeDecision(True, "default")
