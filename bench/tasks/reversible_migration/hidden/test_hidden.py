import sqlite3

import pytest

from db.migrate import connect, downgrade_last, migrations, upgrade_all


def cols(conn):
    return [(r[1], r[2], r[3]) for r in conn.execute("PRAGMA table_info(users)")]


def base_schema():
    conn = connect()
    migrations()[0].upgrade(conn)
    return cols(conn)


def test_upgrade_adds_column():
    conn = connect()
    upgrade_all(conn)
    assert "email" in [c[0] for c in cols(conn)]


def test_unique_index():
    conn = connect()
    upgrade_all(conn)
    conn.execute("INSERT INTO users (name, email) VALUES ('a', 'x@y')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO users (name, email) VALUES ('b', 'x@y')")


def test_downgrade_restores_schema():
    assert len(migrations()) >= 2
    conn = connect()
    upgrade_all(conn)
    downgrade_last(conn)
    assert cols(conn) == base_schema()


def test_downgrade_keeps_rows():
    conn = connect()
    upgrade_all(conn)
    conn.execute("INSERT INTO users (name, email) VALUES ('keep', 'k@y')")
    downgrade_last(conn)
    assert conn.execute("SELECT name FROM users").fetchall() == [("keep",)]
