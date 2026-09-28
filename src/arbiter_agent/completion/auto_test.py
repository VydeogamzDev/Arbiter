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
  run that isn't done in time is delivered with a later hook instead;
- in a repo with many test files (``completion.auto_test_scope: auto``) only the tests of the files
  the session changed run: a full sympy run takes many minutes, one test file 2.5 s (2026-09-27).
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
MAX_RELATED = 6           # test files in one related run
CODE_SUFFIXES = {".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs", ".java", ".kt", ".rb",
                 ".cs", ".c", ".cc", ".cpp", ".h", ".hpp", ".swift", ".php", ".scala", ".toml", ".cfg", ".ini",
                 ".json", ".yaml", ".yml"}


def edit_budget(config: Any, client: str | None) -> float:
    """Seconds an edit's hook waits for Arbiter's test run. Hosts that kill hooks after 5 s get
    ``auto_test_budget_s``; a client listed in ``auto_test_client_budget_s`` gets its own. A result
    that misses the window arrives too late: the agent runs the tests itself (5 of 15 Pi runs with
    a 3 s window, under load, 2026-09-27)."""
    per = config.get("completion.auto_test_client_budget_s") or {}
    if client and isinstance(per, dict) and per.get(client) is not None:
        return float(per[client])
    return float(config.get("completion.auto_test_budget_s", 3.0))


def project_python(root: Path) -> str:
    """The repo's own interpreter when it has a virtualenv (.venv, venv, env), else plain `python`.
    Found on a real install: the system Python couldn't import the project under test."""
    for d in (".venv", "venv", "env"):
        for rel in ("Scripts/python.exe", "bin/python"):
            p = root / d / rel
            if p.is_file():
                return f'"{p}"' if " " in str(p) else str(p)
    return "python"


def _pytest_configured(root: Path, names: set[str]) -> bool:
    if names & {"pytest.ini", "conftest.py"}:
        return True
    for name, marker in (("pyproject.toml", "[tool.pytest"), ("setup.cfg", "[tool:pytest]"), ("tox.ini", "[pytest]")):
        if name in names:
            try:
                if marker in (root / name).read_text("utf-8", errors="replace"):
                    return True
            except OSError:
                pass
    return False


JS_EXTS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")


def _package_json(root: Path) -> dict[str, Any]:
    try:
        d = json.loads((root / "package.json").read_text("utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def runner_kind(root: Path, command: str | None) -> str | None:
    """pytest | jest | vitest | node (another npm test runner) | go | cargo, from the test command and
    package.json."""
    if not command:
        return None
    if "pytest" in command:
        return "pytest"
    if command.startswith("go test"):
        return "go"
    if command.startswith("cargo"):
        return "cargo"
    if "npm test" in command or "jest" in command or "vitest" in command:
        pkg = _package_json(root)
        deps = {**(pkg.get("devDependencies") or {}), **(pkg.get("dependencies") or {})}
        script = str((pkg.get("scripts") or {}).get("test") or "") + " " + command
        if "vitest" in script or "vitest" in deps:
            return "vitest"
        if "jest" in script or "jest" in deps:
            return "jest"
        return "node"
    return None


def detect_command(root: Path) -> str | None:
    """The repo's plain test command, if it has an obvious one."""
    try:
        names = {p.name for p in root.iterdir()}
    except OSError:
        return None
    has_py_tests = (root / "tests").is_dir() or (root / "test").is_dir() or any(
        n.startswith("test_") and n.endswith(".py") for n in names)
    if not has_py_tests and _pytest_configured(root, names):
        # Tests inside the package (sympy/*/tests/test_*.py): nothing at the top level says so, and
        # auto-test was silently off on sympy (2026-09-27).
        has_py_tests = count_test_files(root, stop_at=1) > 0
    if has_py_tests and (names & {"pytest.ini", "conftest.py", "pyproject.toml", "setup.cfg", "tox.ini"}
                         or any(n.endswith(".py") for n in names) or (root / "tests").is_dir()):
        return f"{project_python(root)} -m pytest -q"
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


def is_test_file(name: str) -> bool:
    if name.endswith(".py"):
        return name.startswith("test_") or name.endswith("_test.py")
    if name.endswith("_test.go"):
        return True
    # foo.test.ts, foo.spec.js, and a bare test.ts beside the source (date-fns: addDays/test.ts)
    return name.endswith(JS_EXTS) and (".test." in name or ".spec." in name
                                       or name.split(".", 1)[0] in ("test", "tests", "spec"))


def count_test_files(root: Path, stop_at: int = 1000) -> int:
    n = 0
    for _dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        n += sum(1 for f in filenames if is_test_file(f))
        if n >= stop_at:
            break
    return n


def related_tests(root: Path, changed: list[str]) -> list[str]:
    """Test files for the changed files, by the usual layouts: a changed test file itself, else
    ``test_<module>.py`` in a ``tests``/``test`` dir beside the module or in any parent up to the
    root (``pkg/sub/mod.py`` -> ``pkg/sub/tests/test_mod.py``, ``tests/test_mod.py``). Most recent
    change first; repo-relative, forward slashes."""
    out: list[str] = []
    rroot = root.resolve()
    for raw in reversed(changed):
        try:
            p = Path(raw) if Path(raw).is_absolute() else rroot / raw
            rel = p.resolve().relative_to(rroot)
        except (ValueError, OSError):
            continue
        if rel.suffix != ".py" and not rel.name.endswith(JS_EXTS):
            continue
        found: list[Path] = []
        if is_test_file(rel.name):
            found.append(rel)
        else:
            if rel.suffix == ".py":
                stem = rel.parent.name if rel.name == "__init__.py" else rel.stem
                names: tuple[str, ...] = (f"test_{stem}.py", f"{stem}_test.py")
            else:                    # src/cart.ts -> cart.test.ts, cart.spec.ts, __tests__/cart.test.ts
                stem = rel.name.split(".", 1)[0]
                if stem == "index":      # src/addDays/index.ts -> src/addDays/test.ts (date-fns layout)
                    stem = rel.parent.name
                    found += [rel.parent / f"{t}{ext}" for t in ("test", "tests", "spec") for ext in JS_EXTS
                              if (rroot / rel.parent / f"{t}{ext}").is_file()]
                names = tuple(f"{stem}.{kind}{ext}" for kind in ("test", "spec") for ext in JS_EXTS)
            d = rel.parent
            while True:
                for sub in ("tests", "test", "__tests__", ""):
                    for n in names:
                        cand = d / sub / n if sub else d / n
                        if (rroot / cand).is_file():
                            found.append(cand)
                if not d.parts:
                    break
                d = d.parent
        for f in found:
            t = f.as_posix()
            if t not in out:
                out.append(t)
        if len(out) >= MAX_RELATED:
            break
    return out[:MAX_RELATED]


def _rel_paths(root: Path, changed: list[str]) -> list[str]:
    rroot = root.resolve()
    out: list[str] = []
    for raw in changed:
        try:
            p = Path(raw) if Path(raw).is_absolute() else rroot / raw
            rel = p.resolve().relative_to(rroot).as_posix()
        except (ValueError, OSError):
            continue
        if rel not in out:
            out.append(rel)
    return out


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
        self._slow: set[str] = set()         # "root|command" keys: a related run is judged on its own
        self._timed_out: set[str] = set()
        self._scope: dict[str, str] = {}
        self._warmed: set[str] = set()
        self.stats = {"runs": 0, "reused": 0, "slow_roots": 0}

    def mode(self) -> str:
        return str(self.config.get("completion.auto_test", "off"))

    def scope(self, root: Path) -> str:
        """full | related. ``auto`` picks related for repos with more than
        ``auto_test_related_min_test_files`` test files, when the command is pytest."""
        want = str(self.config.get("completion.auto_test_scope", "auto"))
        if want in ("full", "related"):
            return want
        key = str(root)
        if key not in self._scope:
            kind = runner_kind(root, self.base_command(root))
            many = count_test_files(root) > int(self.config.get("completion.auto_test_related_min_test_files", 40))
            self._scope[key] = "related" if many and kind in ("pytest", "jest", "vitest", "node", "go") else "full"
        return self._scope[key]

    def base_command(self, root: Path) -> str | None:
        forced = self.config.get("completion.auto_test_command")
        return str(forced) if forced else detect_command(root)

    def command(self, root: Path, changed: list[str] | None = None) -> str | None:
        base = self.base_command(root)
        if not base or self.scope(root) != "related":
            return base
        kind = runner_kind(root, base)
        quote = lambda xs: " ".join(f'"{x}"' if " " in x else x for x in xs)  # noqa: E731
        rels = _rel_paths(root, changed or [])
        if kind in ("jest", "vitest"):
            # A file's own tests by name first; the runner's related mode (its module graph) only when
            # there are none: for a widely imported module it selects half the suite (date-fns addDays:
            # 893 tests, 12.5 s, past the post-edit window).
            mapped = related_tests(root, changed or [])
            if mapped:
                return f"npx {'jest' if kind == 'jest' else 'vitest run'} {quote(mapped)}"
            src = [r for r in rels if r.endswith(JS_EXTS)][-MAX_RELATED:]
            if not src:
                return None
            return (f"npx jest --findRelatedTests {quote(src)} --passWithNoTests" if kind == "jest"
                    else f"npx vitest related --run {quote(src)} --passWithNoTests")
        if kind == "go":
            # A Go package is a directory: test the packages the session changed.
            dirs: list[str] = []
            for r in rels:
                if r.endswith(".go"):
                    d = r.rsplit("/", 1)[0] if "/" in r else ""
                    arg = f"./{d}" if d else "."
                    if arg not in dirs:
                        dirs.append(arg)
            return f"go test {' '.join(dirs[-MAX_RELATED:])}" if dirs else None
        tests = related_tests(root, changed or [])
        if not tests:
            return None                    # nothing known to cover the change: don't guess at the whole suite
        if kind == "node":
            return f"{base} -- {quote(tests)}"
        return base + " " + quote(tests)

    def warm(self, root: str | Path, *, wait: bool = False) -> None:
        """Compile a large repo's bytecode into Arbiter's cache once, in the background, so its first
        post-edit test run isn't a cold start: sympy's first related run took 5-9 s cold against
        ~2.5 s warm, past Pi's 8 s window under load (2026-09-27). Only for related-scope (large)
        Python repos; a no-op after the first time for a root."""
        root = Path(root)
        key = str(root)
        with self._lock:
            if key in self._warmed:
                return
            self._warmed.add(key)
        base = self.base_command(root) or ""
        if self.mode() == "off" or "pytest" not in base or self.scope(root) != "related":
            return
        python = base.split(" -m ", 1)[0].strip().strip('"')

        def job() -> None:
            import subprocess

            env = {**os.environ, "PYTHONPYCACHEPREFIX": str(self._pycache(root))}
            try:
                subprocess.run([python, "-m", "compileall", "-q", "-j", "0", str(root)], cwd=root, env=env,
                               capture_output=True, timeout=300,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except (OSError, subprocess.SubprocessError):
                pass

        fut = self._pool.submit(job)
        if wait:
            try:
                fut.result(timeout=300)
            except Exception:
                pass

    def _pycache(self, root: Path, recent_s: float = 120.0) -> Path:
        """Arbiter's own bytecode cache for this repo, minus the entries of recently changed files.

        Python validates a .pyc by its source's size and whole-second mtime, so a same-size edit within
        a second of the last run (a one-character fix) would run stale bytecode and report the old
        result (found by this module's own test). A fresh cache per run avoided that but recompiled
        everything each time: sympy's test runs took 5.3 s instead of 2.5 s and missed Pi's post-edit
        window (2026-09-27). So the cache persists, and bytecode of any file changed in the last
        ``recent_s`` seconds is dropped before each run."""
        import tempfile

        # One cache for everything: the prefix redirects the standard library's and installed packages'
        # bytecode too (pytest, hypothesis), and a per-repo cache recompiled those for every new repo
        # (a first run 7.2 s, the next 2.2 s). Sources map to distinct paths inside it anyway.
        base = Path(tempfile.gettempdir()) / "arbiter-pyc"
        now = time.time()
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for f in filenames:
                if not f.endswith(".py"):
                    continue
                p = os.path.join(dirpath, f)
                try:
                    if now - os.stat(p).st_mtime > recent_s:
                        continue
                except OSError:
                    continue
                # PYTHONPYCACHEPREFIX mirrors the source directory (drive letter dropped on Windows).
                mirror = base / Path(os.path.splitdrive(dirpath)[1].lstrip("\\/"))
                for stale in mirror.glob(f[:-3] + ".*.pyc"):
                    try:
                        stale.unlink()
                    except OSError:
                        pass
        return base

    def _run(self, root: Path, command: str, state: str) -> Outcome:
        timeout = int(self.config.get("completion.auto_test_timeout_s", 120))
        pyc = self._pycache(root)
        res = vr.run_one(vr.VerifyCommand(name="auto_test", run=command, kind="test", timeout_s=timeout), root,
                         extra_env={"PYTHONPYCACHEPREFIX": str(pyc), "PYTHONDONTWRITEBYTECODE": ""})
        out = Outcome(command, res, state, time.time())
        with self._lock:
            self._last[str(root)] = out
            self.stats["runs"] += 1
            key = f"{root}|{command}"
            if res.duration_s > float(self.config.get("completion.auto_test_max_s", 60)) or res.timed_out:
                self._slow.add(key)
                self.stats["slow_roots"] = len(self._slow)
            if res.timed_out:
                self._timed_out.add(key)           # never auto-run a suite that can't finish in time
        return out

    def run(self, root: str | Path, budget_s: float, *, at_stop: bool = False,
            changed: list[str] | None = None) -> Outcome | None:
        """The outcome for the repo's current files: reused if unchanged, else run (waiting up to
        ``budget_s``). None if there's no test command, the suite is slow, or it isn't done in time.
        ``changed``: the files the session changed (related scope runs their tests)."""
        root = Path(root)
        key = str(root)
        command = self.command(root, changed)
        if not command:
            return None
        ck = f"{key}|{command}"
        if ck in self._timed_out or (ck in self._slow and not at_stop):
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
