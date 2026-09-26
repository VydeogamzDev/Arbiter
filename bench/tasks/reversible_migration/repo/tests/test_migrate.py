from db.migrate import connect, upgrade_all


def test_upgrade_creates_users():
    conn = connect()
    upgrade_all(conn)
    conn.execute("INSERT INTO users (name) VALUES ('a')")
