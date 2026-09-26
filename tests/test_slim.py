"""arbiter slim: adds only what the user hasn't set, and removes exactly what it added."""

from __future__ import annotations

import json

import pytest

from arbiter_agent.clients.client_env import current_env
from arbiter_agent.setup import slim


@pytest.fixture
def claude(tmp_path, monkeypatch):
    d = tmp_path / "claude_dir"
    d.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(d))
    monkeypatch.setenv("ARBITER_CLIENT_HOME", str(tmp_path))
    return current_env()


def test_on_off_round_trip_keeps_user_settings(home, claude):
    user = {"model": "opus", "env": {"MY_VAR": "x", "CLAUDE_CODE_DISABLE_ARTIFACT": "0"},
            "permissions": {"allow": ["Bash(git status)"], "deny": ["WebFetch", "ListAgents"]},
            "hooks": {"Stop": []}}
    claude.claude_settings.write_text(json.dumps(user, indent=2) + "\n", encoding="utf-8")
    out = slim.slim_on(home, "lean")
    assert "Done" in out
    data = json.loads(claude.claude_settings.read_text())
    assert data["env"]["CLAUDE_CODE_DISABLE_ARTIFACT"] == "0"          # the user's own value wins
    assert data["env"]["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
    assert data["permissions"]["allow"] == ["Bash(git status)"]         # allow rules untouched
    assert "ScheduleWakeup" in data["permissions"]["deny"] and data["permissions"]["deny"].count("ListAgents") == 1
    assert "on (lean" in slim.status(home)
    assert "already on" in slim.slim_on(home, "standard")
    slim.slim_off(home)
    assert json.loads(claude.claude_settings.read_text()) == user       # exactly the original back
    assert "off" in slim.status(home)


def test_dry_run_writes_nothing(home, claude):
    out = slim.slim_on(home, "standard", dry_run=True)
    assert "dry run" in out and not claude.claude_settings.exists() and slim.load_record(home) is None


def test_settings_fragment_for_the_benchmark():
    frag = slim.settings_for("lean")
    assert frag["env"]["CLAUDE_CODE_DISABLE_ARTIFACT"] == "1" and "CronCreate" in frag["permissions"]["deny"]
    assert "Read" not in frag["permissions"]["deny"] and "ToolSearch" not in frag["permissions"]["deny"]
