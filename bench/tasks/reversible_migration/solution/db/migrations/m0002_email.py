def upgrade(conn):
    conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
    conn.execute("CREATE UNIQUE INDEX users_email ON users(email)")


def downgrade(conn):
    conn.execute("DROP INDEX IF EXISTS users_email")
    conn.execute("CREATE TABLE users_old (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    conn.execute("INSERT INTO users_old (id, name) SELECT id, name FROM users")
    conn.execute("DROP TABLE users")
    conn.execute("ALTER TABLE users_old RENAME TO users")
