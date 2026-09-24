"""Session baseline for test integrity (spec §12.4).

Captured at session start (or the first event with a cwd, or ``arbiter verify --baseline``):
git HEAD and dirtiness, plus per-file metrics for test files, fixtures, snapshots and
harness configuration. Metrics (test/assert/skip counts) are stored instead of content so
later comparisons don't need the old text.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from arbiter_agent.state.repo_identity import RepoIdentity, git, identify, path_key

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build", "target", ".tox",
             ".nox", ".mypy_cache", ".ruff_cache", ".pytest_cache", ".next", ".nuxt", "coverage", ".gradle", "bin",
             "obj", ".idea", ".vscode", "vendor", ".cache", "site-packages", ".arbiter"}
MAX_FILES = 5000
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_WALK_S = 10.0

TEST_FILE = re.compile(
    r"(^|/)(tests?|__tests__|spec|specs|testing)/|(^|/)test_[^/]+\.py$|_test\.(py|go|rb|exs?)$|"
    r"\.(test|spec)\.[cm]?[jt]sx?$|(^|/)[^/]+Tests?\.(cs|java|kt|swift)$|(^|/)conftest\.py$|_spec\.rb$", re.I)
FIXTURE_FILE = re.compile(r"(^|/)(fixtures?|testdata|test_data|golden|goldens|__snapshots__|snapshots|cassettes)/|"
                          r"\.snap$|\.golden$|\.approved\.", re.I)
HARNESS_FILE = re.compile(
    r"(^|/)(pytest\.ini|tox\.ini|noxfile\.py|setup\.cfg|pyproject\.toml|conftest\.py|jest\.config\.[cm]?[jt]s|"
    r"jest\.config\.json|vitest\.config\.[cm]?[jt]s|vite\.config\.[cm]?[jt]s|\.mocharc(\.\w+)?|karma\.conf\.js|"
    r"playwright\.config\.[cm]?[jt]s|cypress\.config\.[cm]?[jt]s|package\.json|Cargo\.toml|go\.mod|Makefile|"
    r"justfile|phpunit\.xml(\.dist)?|\.coveragerc|codecov\.ya?ml|\.nycrc(\.json)?|Directory\.Build\.props|"
    r"[^/]+\.runsettings|build\.gradle(\.kts)?|pom\.xml)$", re.I)
CODE_EXT = re.compile(r"\.(py|[cm]?[jt]sx?|go|rs|rb|java|kt|cs|swift|php|exs?|scala|cpp|cc|c|h)$", re.I)

TEST_DEF = re.compile(r"^\s*(?:async\s+)?def\s+test\w*\s*\(|^\s*(?:it|test|specify)(?:\.each\([^)]*\))?\s*\(\s*['\"`]|"
                      r"^\s*func\s+Test\w+\s*\(|^\s*#\[(?:tokio::)?test\]|^\s*@Test\b|^\s*\[(?:Fact|Theory|Test|"
                      r"TestMethod)\]|^\s*def\s+test_\w+|^\s*it\s+['\"]", re.M)
ASSERT = re.compile(r"\bassert\w*\b|\bexpect\s*\(|\bshould\.|\bt\.(?:Error|Fatal|Fail)\w*\(|\brequire\.\w+\(|"
                    r"\bassert_eq!|\bassert_ne!|\bassert!|\bAssert\.\w+\(|\bself\.fail\(|\bpytest\.raises\(")
SKIP = re.compile(r"@pytest\.mark\.(?:skip|skipif|xfail)\b|\bpytest\.skip\(|\bunittest\.skip\w*\b|@skip\w*\b|"
                  r"\b(?:it|test|describe)\.(?:skip|todo)\s*\(|\b(?:xit|xtest|xdescribe)\s*\(|\bt\.Skip\w*\(|"
                  r"#\[ignore\]|@Ignore\b|@Disabled\b|\[Ignore\]|Skip\s*=\s*\"|\.only\s*\(|\bfit\s*\(|\bfdescribe\s*\(")
FILTER = re.compile(r"--deselect\b|(?:^|\s)-k\s|--ignore(?:-glob)?\b|testPathIgnorePatterns|modulePathIgnorePatterns|"
                    r"collect_ignore|norecursedirs|--testNamePattern|--grep\b|\bexclude\b|\bomit\b|--skip\b|"
                    r"fail_under|--cov-fail-under|coverageThreshold|-p\s+no:|--exclude\b|passWithNoTests|"
                    r"\bbail\s*[:=]\s*0|\"test\"\s*:\s*\"[^\"]*(?:\|\||;)\s*(?:true|exit 0)")


@dataclass
class FileMetrics:
    sha: str
    size: int
    role: str                 # test | fixture | harness
    tests: int = 0
    asserts: int = 0
    skips: int = 0
    filters: int = 0          # harness: test-selection / exclusion / threshold settings


@dataclass
class Baseline:
    id: str
    session_id: str
    repo: dict[str, Any]
    head: str | None
    dirty: bool | None
    files: dict[str, FileMetrics] = field(default_factory=dict)
    captured_at: float = 0.0
    truncated: bool = False

    def to_row(self) -> tuple[Any, ...]:
        tests = {k: asdict(v) for k, v in self.files.items() if v.role != "harness"}
        harness = {k: asdict(v) for k, v in self.files.items() if v.role == "harness"}
        dirty = None if self.dirty is None else int(self.dirty)
        return (self.id, self.session_id, json.dumps(self.repo), self.head, dirty,
                json.dumps({"files": tests, "truncated": self.truncated}), json.dumps(harness), None, self.captured_at)


def role_of(key: str) -> str | None:
    if HARNESS_FILE.search(key):
        return "harness"
    if FIXTURE_FILE.search(key):
        return "fixture"
    if TEST_FILE.search(key) and (CODE_EXT.search(key) or key.endswith("conftest.py")):
        return "test"
    return None


def measure(path: Path, role: str) -> FileMetrics | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) > MAX_FILE_BYTES:
        return FileMetrics(hashlib.sha256(data).hexdigest(), len(data), role)
    m = FileMetrics(hashlib.sha256(data).hexdigest(), len(data), role)
    if role == "test" or (role == "harness" and path.name == "conftest.py"):
        text = data.decode("utf-8", "replace")
        m.tests = len(TEST_DEF.findall(text))
        m.asserts = len(ASSERT.findall(text))
        m.skips = len(SKIP.findall(text))
    if role == "harness":
        m.filters = len(FILTER.findall(data.decode("utf-8", "replace")))
    return m


def scan(ident: RepoIdentity, max_s: float | None = None) -> tuple[dict[str, FileMetrics], bool]:
    """Walk the repo for test/fixture/harness files. Bounded by file count and time."""
    limit = MAX_WALK_S if max_s is None else max(0.05, max_s)
    root = Path(ident.root)
    out: dict[str, FileMetrics] = {}
    t0 = time.monotonic()
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            p = Path(dirpath) / fn
            key = path_key(p, ident)
            role = role_of(key)
            if role is None:
                continue
            m = measure(p, role)
            if m is not None:
                out[key] = m
            if len(out) >= MAX_FILES:
                return out, True
        if time.monotonic() - t0 > limit:
            truncated = True
            break
    return out, truncated


def capture(session_id: str, cwd: str) -> Baseline:
    ident = identify(cwd)
    files, truncated = scan(ident)
    dirty: bool | None = None
    if ident.in_repo:
        st = git(ident.root, "status", "--porcelain", "--untracked-files=no")
        dirty = None if st is None else bool(st.strip())
    bid = hashlib.sha256(f"{session_id}|{time.time()}".encode()).hexdigest()[:16]
    return Baseline(bid, session_id, ident.to_dict(), ident.head, dirty, files, time.time(), truncated)


def store(conn: sqlite3.Connection, b: Baseline) -> None:
    conn.execute("INSERT OR REPLACE INTO session_baseline(id, session_id, repo_identity_json, head, dirty, "
                 "test_file_hashes_json, harness_config_hashes_json, test_inventory_pointer, captured_at) "
                 "VALUES (?,?,?,?,?,?,?,?,?)", b.to_row())
    conn.execute("UPDATE session_state SET baseline_id = ?, updated_at = ? WHERE session_id = ?",
                 (b.id, time.time(), b.session_id))


def load(conn: sqlite3.Connection, session_id: str) -> Baseline | None:
    row = conn.execute("SELECT id, session_id, repo_identity_json, head, dirty, test_file_hashes_json, "
                       "harness_config_hashes_json, captured_at FROM session_baseline WHERE session_id = ? "
                       "ORDER BY captured_at DESC LIMIT 1", (session_id,)).fetchone()
    if not row:
        return None
    tests = json.loads(row[5] or "{}")
    files = {k: FileMetrics(**v) for k, v in (tests.get("files") or {}).items()}
    files.update({k: FileMetrics(**v) for k, v in json.loads(row[6] or "{}").items()})
    return Baseline(row[0], row[1], json.loads(row[2] or "{}"), row[3], None if row[4] is None else bool(row[4]),
                    files, float(row[7] or 0), bool(tests.get("truncated")))
