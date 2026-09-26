import secrets


class TokenStore:
    """API tokens per user."""

    def __init__(self, records=None):
        self.records = dict(records or {})    # user -> stored token

    def issue(self, user):
        token = secrets.token_urlsafe(16)
        self.records[user] = token
        return token

    def verify(self, user, token):
        return self.records.get(user) == token
