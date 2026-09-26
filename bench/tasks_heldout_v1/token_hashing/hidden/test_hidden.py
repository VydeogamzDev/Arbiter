import hashlib
import pathlib

from auth.store import TokenStore


def test_no_plaintext():
    s = TokenStore()
    t = s.issue("ann")
    assert t not in s.records["ann"] and s.records["ann"].startswith("sha256$")


def test_verify_ok_and_wrong():
    s = TokenStore()
    t = s.issue("ann")
    assert s.verify("ann", t) and not s.verify("ann", t + "x") and not s.verify("bob", t)


def test_format_and_unique_salt():
    s = TokenStore()
    s.issue("a")
    s.issue("b")
    parts = [s.records[u].split("$") for u in ("a", "b")]
    assert all(len(p) == 3 and p[0] == "sha256" for p in parts)
    assert parts[0][1] != parts[1][1]
    for u, p in zip(("a", "b"), parts):
        bytes.fromhex(p[1])
        assert len(p[2]) == 64


def test_constant_time():
    src = pathlib.Path("auth/store.py").read_text()
    assert "compare_digest" in src


def test_legacy_upgrade():
    s = TokenStore({"old": "legacy-token"})
    assert s.verify("old", "legacy-token")
    rec = s.records["old"]
    assert rec.startswith("sha256$") and "legacy-token" not in rec
    assert s.verify("old", "legacy-token")


def test_legacy_wrong_unchanged():
    s = TokenStore({"old": "legacy-token"})
    assert not s.verify("old", "nope")
    assert s.records["old"] == "legacy-token"
