import hashlib
import hmac
import os


def hash_password(password, salt=None):
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return salt.hex() + ":" + digest.hex()


def check_password(password, stored):
    salt_hex, _ = stored.split(":")
    return hmac.compare_digest(hash_password(password, bytes.fromhex(salt_hex)), stored)


def login(session, email, password):
    rows = session.query("SELECT id, password_hash FROM users WHERE email = ?", (email,))
    if not rows or not check_password(password, rows[0][1]):
        raise PermissionError("bad credentials")
    return rows[0][0]
