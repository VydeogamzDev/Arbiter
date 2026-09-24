import sqlite3
import subprocess
import sys
import textwrap
import time

import pytest

from arbiter_agent.state.store import Migration, connect, integrity_ok, migrate, packaged_migrations
from arbiter_agent.state.writer import ReadOnlyDegraded, Writer


def test_fresh_db_migrates_with_wal_fk_and_schema(home):
    res = migrate(home.db, home.backups)
    assert not res.degraded and res.applied == [m.version for m in packaged_migrations()] and res.backup is None
    conn = connect(home.db)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA auto_vacuum").fetchone()[0] == 2  # incremental
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("event_log", "client", "client_session", "install_manifest", "thread", "checkpoint", "context_item",
              "intent", "contract", "session_baseline", "verification", "utility_decision", "speculative_action",
              "branch_run", "blob", "watcher_offset", "scope_skip"):
        assert t in tables, t
    conn.close()
    assert migrate(home.db, home.backups).applied == []  # idempotent


def _insert_event(conn, key="k1"):
    conn.execute("INSERT INTO event_log(client_id, surface, event_type, raw_hash, received_at, idempotency_key) "
                 "VALUES ('fake', 'hook', 'stop', 'h', ?, ?)", (time.time(), key))


def test_event_log_is_append_only(home):
    migrate(home.db, home.backups)
    conn = connect(home.db)
    _insert_event(conn)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE event_log SET event_type = 'x'")
    with pytest.raises(sqlite3.IntegrityError, match="only be deleted by retention"):
        conn.execute("DELETE FROM event_log")
    conn.execute("UPDATE retention_gate SET open = 1")
    conn.execute("DELETE FROM event_log")  # allowed only through the gate
    conn.execute("UPDATE retention_gate SET open = 0")
    conn.close()


def test_failed_migration_restores_backup_and_degrades(home):
    migrate(home.db, home.backups)
    conn = connect(home.db)
    _insert_event(conn, "before-bad-migration")
    conn.close()
    bad = packaged_migrations() + [Migration(999, "broken", "CREATE TABLE ok_part (x INTEGER);\nTHIS IS NOT SQL;")]
    res = migrate(home.db, home.backups, bad)
    assert res.degraded and "0999_broken" in (res.error or "")
    assert res.backup is not None and res.backup.exists()
    conn = connect(home.db, readonly=True)
    assert conn.execute("SELECT COUNT(*) FROM event_log").fetchone()[0] == 1  # data intact
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "ok_part" not in tables  # partial migration rolled back
    assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == packaged_migrations()[-1].version
    conn.close()
    w = Writer(home.db, degraded=True).start()
    with pytest.raises(ReadOnlyDegraded):
        w.submit(lambda c: None)
    w.stop()


def test_writer_serializes_and_drains(home):
    migrate(home.db, home.backups)
    w = Writer(home.db).start()
    futs = [w.submit(lambda c, i=i: _insert_event(c, f"k{i}")) for i in range(200)]
    w.stop()  # drain before exit
    assert all(f.done() for f in futs)
    conn = connect(home.db, readonly=True)
    assert conn.execute("SELECT COUNT(*) FROM event_log").fetchone()[0] == 200
    conn.close()


CRASH_WRITER = textwrap.dedent("""
    import sys, time
    from pathlib import Path
    from arbiter_agent.state.store import connect
    conn = connect(Path(sys.argv[1]))
    i = 0
    while True:
        conn.execute("BEGIN IMMEDIATE")
        for j in range(5):
            conn.execute("INSERT INTO event_log(client_id, surface, event_type, raw_hash, received_at, idempotency_key)"
                         " VALUES ('fake','hook','stop','h',?,?)", (time.time(), f"crash-{i}-{j}"))
        conn.execute("COMMIT")
        print(i, flush=True)
        i += 1
""")


def test_crash_mid_write_recovers_committed_prefix(home, tmp_path):
    migrate(home.db, home.backups)
    script = tmp_path / "writer.py"
    script.write_text(CRASH_WRITER)
    proc = subprocess.Popen([sys.executable, str(script), str(home.db)], stdout=subprocess.PIPE, text=True)
    acked = -1
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and acked < 150:
        line = proc.stdout.readline()
        if line.strip():
            acked = int(line)
    proc.kill()  # hard kill: TerminateProcess / SIGKILL, mid-transaction most of the time
    proc.wait()
    assert acked >= 150
    assert integrity_ok(home.db)
    conn = connect(home.db)
    rows = conn.execute("SELECT COUNT(*) FROM event_log").fetchone()[0]
    conn.close()
    assert rows >= (acked + 1) * 5  # every acknowledged commit survived
    assert rows % 5 == 0  # no torn transaction
