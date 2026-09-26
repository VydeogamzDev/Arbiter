import hashlib
import hmac
import secrets


def _hash(token, salt):
    return hashlib.sha256(salt + token.encode("utf-8")).hexdigest()


def _encode(token):
    salt = secrets.token_bytes(16)
    return f"sha256${salt.hex()}${_hash(token, salt)}"


class TokenStore:
    """API tokens per user, stored as salted SHA-256 hashes."""

    def __init__(self, records=None):
        self.records = dict(records or {})    # user -> "sha256$salt$hash" (or a legacy plaintext token)

    def issue(self, user):
        token = secrets.token_urlsafe(16)
        self.records[user] = _encode(token)
        return token

    def verify(self, user, token):
        stored = self.records.get(user)
        if stored is None:
            return False
        if stored.startswith("sha256$"):
            _, salt_hex, digest = stored.split("$", 2)
            return hmac.compare_digest(_hash(token, bytes.fromhex(salt_hex)), digest)
        ok = hmac.compare_digest(stored.encode("utf-8"), token.encode("utf-8"))
        if ok:
            self.records[user] = _encode(token)
        return ok
