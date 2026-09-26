import importlib
import pkgutil
import sqlite3

import db.migrations as pkg


def migrations():
    names = sorted(m.name for m in pkgutil.iter_modules(pkg.__path__) if m.name.startswith("m"))
    return [importlib.import_module(f"db.migrations.{n}") for n in names]


def upgrade_all(conn):
    for m in migrations():
        m.upgrade(conn)


def downgrade_last(conn):
    migrations()[-1].downgrade(conn)


def connect(path=":memory:"):
    return sqlite3.connect(path)
