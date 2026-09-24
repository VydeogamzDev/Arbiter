"""SQLite store (spec §16.1, §16.5).

- WAL journal, foreign keys, busy timeout, incremental auto-vacuum.
- Migrations run only at open, after an automatic backup of the database file.
- A failed migration restores the backup and opens the store read-only ("degraded").
- Only the Writer (state/writer.py) writes; readers get their own read-only connections.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

MIGRATION_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


def packaged_migrations() -> list[Migration]:
    out = []
    pkg = resources.files("arbiter_agent.state").joinpath("migrations")
    for entry in pkg.iterdir():
        m = MIGRATION_RE.match(entry.name)
        if m:
            out.append(Migration(int(m.group(1)), m.group(2), entry.read_text(encoding="utf-8")))
    return sorted(out, key=lambda m: m.version)


def connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5.0,
                               check_same_thread=False, isolation_level=None)
    else:
        conn = sqlite3.connect(str(path), timeout=5.0, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    if not readonly:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def current_version(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                 "applied_at REAL NOT NULL)")
    row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return int(row[0] or 0)


def backup_database(src: Path, backups: Path, label: str) -> Path | None:
    if not src.exists():
        return None
    backups.mkdir(parents=True, exist_ok=True)
    dest = backups / f"{src.stem}-{time.strftime('%Y%m%d-%H%M%S')}-{label}.sqlite"
    s = sqlite3.connect(str(src))
    d = sqlite3.connect(str(dest))
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()
    return dest


@dataclass
class OpenResult:
    version: int
    applied: list[int] = field(default_factory=list)
    backup: Path | None = None
    degraded: bool = False
    error: str | None = None


def migrate(db: Path, backups: Path, migrations: list[Migration] | None = None) -> OpenResult:
    """Apply pending migrations. On failure restore the pre-migration backup and report degraded."""
    migrations = packaged_migrations() if migrations is None else migrations
    is_new = not db.exists()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db)
    try:
        if is_new:
            conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
            conn.execute("VACUUM")
        cur = current_version(conn)
        pending = [m for m in migrations if m.version > cur]
        if not pending:
            return OpenResult(version=cur)
    finally:
        conn.close()

    backup = None if is_new else backup_database(db, backups, f"pre-v{pending[0].version}")
    conn = connect(db)
    applied: list[int] = []
    try:
        for m in pending:
            try:
                conn.execute("BEGIN IMMEDIATE")
                for stmt in _split_sql(m.sql):
                    conn.execute(stmt)
                conn.execute("INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                             (m.version, m.name, time.time()))
                conn.execute("COMMIT")
                applied.append(m.version)
            except sqlite3.Error as exc:
                conn.execute("ROLLBACK")
                raise MigrationError(f"migration {m.version:04d}_{m.name} failed: {exc}") from exc
        return OpenResult(version=pending[-1].version, applied=applied, backup=backup)
    except MigrationError as exc:
        conn.close()
        if backup is not None:
            _restore(backup, db)
        return OpenResult(version=_version_of(db), applied=[], backup=backup, degraded=True, error=str(exc))
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def _restore(backup: Path, db: Path) -> None:
    for suffix in ("-wal", "-shm"):
        p = db.with_name(db.name + suffix)
        if p.exists():
            p.unlink()
    shutil.copyfile(backup, db)


def _version_of(db: Path) -> int:
    conn = connect(db)
    try:
        return current_version(conn)
    finally:
        conn.close()


def _split_sql(script: str) -> list[str]:
    """Split a migration into statements, keeping trigger bodies (BEGIN ... END;) intact."""
    stmts: list[str] = []
    buf: list[str] = []
    depth = 0
    for line in script.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") and not buf:
            continue
        buf.append(line)
        upper = stripped.upper()
        if upper == "BEGIN" or upper.endswith(" BEGIN"):
            depth += 1
        if depth and (upper == "END;" or upper.startswith("END;")):
            depth -= 1
            if depth == 0:
                stmts.append("\n".join(buf))
                buf = []
            continue
        if depth == 0 and stripped.endswith(";"):
            stmt = "\n".join(buf).strip()
            if stmt and not all(ln.strip().startswith("--") or not ln.strip() for ln in buf):
                stmts.append(stmt)
            buf = []
    tail = "\n".join(buf).strip()
    if tail and not tail.startswith("--"):
        stmts.append(tail)
    return stmts


def integrity_ok(db: Path) -> bool:
    conn = connect(db, readonly=True)
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def storage_bytes(db: Path) -> int:
    total = 0
    for suffix in ("", "-wal", "-shm"):
        p = db.with_name(db.name + suffix)
        if p.exists():
            total += p.stat().st_size
    return total
