"""Arbiter runs the repository's tests itself (``completion.auto_test``; benchmark finding 2026-09-26).

Test runs were 26% of Opus 5.5's API calls and ~15% of Sonnet 5's in the A/B benchmark, and every
call re-reads the whole context. So after a code edit Arbiter runs the detected test command and
hands the result back in the edit's hook response ("tests after this edit: 12 passed"), and at a
completion claim with no fresh passing run it runs the tests instead of sending the agent back.

Guardrails:
- only a detected runner (pytest, npm test, cargo test, go test) or ``completion.auto_test_command``;
- results are reused while the repository's files are unchanged (a cheap stat fingerprint);
- a suite slower than ``completion.auto_test_max_s`` is remembered as slow and not auto-run again
  after edits (it still runs, once, at a completion claim if nothing else verified the change);
- runs are bounded by ``completion.auto_test_timeout_s`` and never block a hook past its budget: a
  run that isn't done in time is delivered with a later hook instead.
"""

from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from arbiter_agent.completion import verify_runner as vr

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache", ".mypy_cache", ".ruff_cache",
             "dist", "build", "target", ".tox", ".idea", ".vscode", ".arbiter"}
MAX_FINGERPRINT_FILES = 5000
CODE_SUFFIXES = {".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs", ".java", ".kt", ".rb",
                 ".cs", ".c", ".cc", ".cpp", ".h", ".hpp", ".swift", ".php", ".scala", ".toml", ".cfg", ".ini",
                 ".json", ".yaml", ".yml"}


def detect_command(root: Path) -> str | None:
    """The repo's plain test command, if it has an obvious one."""
    try:
        names = {p.name for p in root.iterdir()}
    except OSError:
        return None
    has_py_tests = (root / "tests").is_dir() or (root / "test").is_dir() or any(
        n.startswith("test_") and n.endswith(".py") for n in names)
    if has_py_tests and (names & {"pytest.ini", "conftest.py", "pyproject.toml", "setup.cfg", "tox.ini"}
                         or any(n.endswith(".py") for n in names) or (root / "tests").is_dir()):
        return "python -m pytest -q"
    if "package.json" in names:
        try:
            scripts = json.loads((root / "package.json").read_text("utf-8")).get("scripts") or {}
        except (OSError, ValueError):
            scripts = {}
        if "test" in scripts and "no test specified" not in str(scripts["test"]):
            return "npm test --silent"
    if "Cargo.toml" in names:
        return "cargo test -q"
    if "go.mod" in names:
        return "go test ./..."
    return None


def is_code_path(path: str) -> bool:
    return Path(path).suffix.lower() in CODE_SUFFIXES


def fingerprint(root: Path, recent_s: float = 30.0) -> str:
    """A cheap identity of the repo's current files: paths, sizes and mtimes, plus the contents of
    files changed in the last ``recent_s`` seconds (two quick same-size edits can share an mtime tick,
    which once made a stale PASS look current)."""
    h = hashlib.sha256()
    n = 0
    now = time.time()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for f in sorted(filenames):
            if f.endswith((".pyc", ".log")):
                continue
            p = os.path.join(dirpath, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            h.update(f"{p}|{st.st_size}|{st.st_mtime_ns}\n".encode())
            if now - st.st_mtime < recent_s and st.st_size < 2_000_000:
                try:
                    with open(p, "rb") as fh:
                        h.update(hashlib.sha256(fh.read()).digest())
                except OSError:
                    pass
            n += 1
            if n >= MAX_FINGERPRINT_FILES:
                return h.hexdigest()
    return h.hexdigest()


@dataclass
class Outcome:
    command: str
    result: vr.VerifyResult
    state: str
    finished_at: float

    def summary(self, max_chars: int = 600) -> str:
        r = self.result
        runner = r.runner or {}
        passed, failed, errors = runner.get("passed"), runner.get("failed"), runner.get("errors")
        counts = ", ".join(f"{v} {k}" for k, v in (("passed", passed), ("failed", failed), ("errors", errors))
                           if isinstance(v, int) and v)
        head = (f"[Arbiter] Ran `{self.command}` on the current files: "
                f"{r.status.upper()}{f' ({counts})' if counts else ''} in {r.duration_s:.1f}s.")
        if r.status == "pass" or not r.output_tail:
            return head
        tail = r.output_tail.strip()
        lines = [ln for ln in tail.splitlines() if ln.strip()]
        keep = [ln for ln in lines if any(k in ln for k in ("FAILED", "Error", "error", "assert", "Traceback",
                                                           "failed", "E  "))][-8:] or lines[-8:]
        return (head + " Failure output (tail):\n" + "\n".join(keep))[:max_chars]


class AutoTester:
    def __init__(self, config: Any) -> None:
        self.config = config
        self._lock = threading.Lock()
        self._pool = cf.ThreadPoolExecutor(max_workers=2, thread_name_prefix="auto-test")
        self._inflight: dict[str, tuple[str, cf.Future[Outcome]]] = {}   # root -> (state, future)
        self._last: dict[str, Outcome] = {}
        self._slow: set[str] = set()
        self._timed_out: set[str] = set()
        self.stats = {"runs": 0, "reused": 0, "slow_roots": 0}

    def mode(self) -> str:
        return str(self.config.get("completion.auto_test", "off"))

    def command(self, root: Path) -> str | None:
        forced = self.config.get("completion.auto_test_command")
        return str(forced) if forced else detect_command(root)

    def _run(self, root: Path, command: str, state: str) -> Outcome:
        import tempfile

        timeout = int(self.config.get("completion.auto_test_timeout_s", 120))
        # A private bytecode cache per run: Python validates .pyc files by source size and whole-second
        # mtime, so a same-size edit within a second of the last run (a one-character fix) would run
        # stale bytecode and report the old result. Found by this module's own test.
        with tempfile.TemporaryDirectory(prefix="arbiter-pyc-") as pyc:
            res = vr.run_one(vr.VerifyCommand(name="auto_test", run=command, kind="test", timeout_s=timeout), root,
                             extra_env={"PYTHONPYCACHEPREFIX": pyc, "PYTHONDONTWRITEBYTECODE": ""})
        out = Outcome(command, res, state, time.time())
        with self._lock:
            self._last[str(root)] = out
            self.stats["runs"] += 1
            if res.duration_s > float(self.config.get("completion.auto_test_max_s", 60)) or res.timed_out:
                self._slow.add(str(root))
                self.stats["slow_roots"] = len(self._slow)
            if res.timed_out:
                self._timed_out.add(str(root))     # never auto-run a suite that can't finish in time
        return out

    def run(self, root: str | Path, budget_s: float, *, at_stop: bool = False) -> Outcome | None:
        """The outcome for the repo's current files: reused if unchanged, else run (waiting up to
        ``budget_s``). None if there's no test command, the suite is slow, or it isn't done in time."""
        root = Path(root)
        key = str(root)
        if key in self._timed_out or (key in self._slow and not at_stop):
            return None
        command = self.command(root)
        if not command:
            return None
        state = fingerprint(root)
        with self._lock:
            last = self._last.get(key)
            if last is not None and last.state == state and last.command == command:
                self.stats["reused"] += 1
                return last
            inflight = self._inflight.get(key)
            if inflight is None or inflight[0] != state:
                fut = self._pool.submit(self._run, root, command, state)
                self._inflight[key] = (state, fut)
            else:
                fut = inflight[1]
        try:
            return fut.result(timeout=max(0.0, budget_s))
        except cf.TimeoutError:
            return None
        except Exception:
            return None

    def ready(self, root: str | Path) -> Outcome | None:
        """A finished outcome for the repo's current files, without starting anything."""
        key = str(Path(root))
        with self._lock:
            last = self._last.get(key)
        if last is None:
            return None
        return last if last.state == fingerprint(Path(root)) else None

    def stop(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
