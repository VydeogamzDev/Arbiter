import os

import pytest

from arbiter_agent.privacy.redaction import Redactor, load_install_key
from arbiter_agent.privacy.scope import ProjectScope
from arbiter_agent.privacy.secrets import find_secrets

KEY = b"k" * 32

SECRETS = {
    "openai_key": "sk-proj-" + "A1b2C3d4" * 5,
    "anthropic_key": "sk-ant-api03-" + "Zz9" * 10,
    "github_token": "ghp_" + "a" * 36,
    "aws_access_key_id": "AKIA" + "ABCDEFGHIJKLMNOP",
    "slack_token": "xoxb-1234567890-abcdefghij",
    "google_api_key": "AIza" + "B" * 35,
    "stripe_key": "sk_live_" + "c" * 24,
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
}


@pytest.mark.parametrize("kind, secret", list(SECRETS.items()))
def test_known_token_formats_redacted(kind, secret):
    r = Redactor(KEY)
    out = r.redact_text(f"here it is: {secret} ok")
    assert secret not in out
    assert "[REDACTED:" in out and out.endswith(" ok")


def test_context_patterns():
    r = Redactor(KEY)
    text = ("export DATABASE_PASSWORD=hunter2hunter2\n"
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345\n"
            "url=https://bob:s3cretpw@example.com/db\n"
            '{"client_secret": "shh-very-secret-value"}\n'
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----")
    out = r.redact_text(text)
    for s in ("hunter2hunter2", "abcdefghijklmnopqrstuvwxyz012345", "s3cretpw", "shh-very-secret-value", "MIIEow"):
        assert s not in out
    assert "DATABASE_PASSWORD=" in out  # key names stay readable
    assert "Bearer [REDACTED:bearer_token:" in out


def test_same_secret_same_placeholder_different_key_differs():
    s = SECRETS["github_token"]
    a = Redactor(KEY).redact_text(s)
    b = Redactor(KEY).redact_text(s)
    c = Redactor(b"x" * 32).redact_text(s)
    assert a == b and a != c


def test_object_redaction_and_keyed_fields():
    r = Redactor(KEY)
    obj = {"tool_input": {"command": f"curl -H 'x: {SECRETS['openai_key']}'"}, "api_key": "Qm9vbTEyMzQ1Njc4OTAh",
           "note": "plain text stays", "n": 3, "items": [SECRETS["slack_token"]]}
    out = r.redact(obj)
    flat = repr(out)
    assert SECRETS["openai_key"] not in flat and "Qm9vbTEyMzQ1Njc4OTAh" not in flat
    assert SECRETS["slack_token"] not in flat
    assert out["note"] == "plain text stays" and out["n"] == 3
    assert r.total >= 3


def test_no_false_positive_on_ordinary_text():
    text = "Ran 12 tests in 0.4s. OK. token count 1234. password field validation added in forms.py"
    assert list(find_secrets(text)) == []


def test_install_key_created_once(home):
    k1 = load_install_key(home.install_key_file)
    k2 = load_install_key(home.install_key_file)
    assert k1 == k2 and len(k1) == 32


def test_scope_default_exclude_include(home, tmp_path):
    proj = tmp_path / "proj"
    (proj / "sub").mkdir(parents=True)
    s = ProjectScope(home.scope_file)
    assert s.decide(str(proj / "sub")).in_scope
    s.exclude(str(proj))
    d = s.decide(str(proj / "sub"))
    assert not d.in_scope and d.reason == "cli_exclude"
    s.include(str(proj))
    assert s.decide(str(proj / "sub")).in_scope


def test_scope_arbiterignore_and_config_glob(home, tmp_path):
    proj = tmp_path / "secretproj"
    (proj / "deep" / "er").mkdir(parents=True)
    (proj / ".arbiterignore").write_text("")
    s = ProjectScope(home.scope_file, config_excludes=[str(tmp_path / "private*")])
    d = s.decide(str(proj / "deep" / "er"))
    assert not d.in_scope and d.reason == "arbiterignore"
    (tmp_path / "private-work").mkdir()
    assert s.decide(str(tmp_path / "private-work")).reason == "config_exclude"


def test_scope_allow_list(home, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    s = ProjectScope(home.scope_file, mode="allow_list")
    assert not s.decide(str(a)).in_scope
    s.include(str(a))
    assert s.decide(str(a)).in_scope and not s.decide(str(b)).in_scope
    assert not s.decide(None).in_scope


@pytest.mark.skipif(os.name != "nt", reason="case-insensitive path handling is Windows-specific")
def test_scope_case_insensitive_on_windows(home, tmp_path):
    proj = tmp_path / "CaseProj"
    proj.mkdir()
    s = ProjectScope(home.scope_file)
    s.exclude(str(proj).upper())
    assert not s.decide(str(proj).lower()).in_scope
