import sqlite3


class Session:
    def __init__(self, url):
        self.conn = sqlite3.connect(url.removeprefix("sqlite:///"))

    def query(self, sql, params=()):
        return self.conn.execute(sql, params).fetchall()


def open_session(url):
    return Session(url)
