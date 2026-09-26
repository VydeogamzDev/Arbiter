def upgrade(conn):
    conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
    conn.execute("CREATE UNIQUE INDEX users_email ON users(email)")


def downgrade(conn):
    pass
