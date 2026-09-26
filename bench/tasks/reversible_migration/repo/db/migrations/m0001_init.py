def upgrade(conn):
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")


def downgrade(conn):
    conn.execute("DROP TABLE users")
