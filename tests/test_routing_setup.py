"""`arbiter routing`: opt-in, reversible; only Arbiter's config and (with --codex-default) Codex's
top-level model / model_reasoning_effort change, and disable restores what was there."""

from __future__ import annotations

import yaml

from arbiter_agent.clients.client_env import ClientEnv
from arbiter_agent.setup import routing

CODEX = """# my config
model = "gpt-6-sol"
model_reasoning_effort = "high"  # I like it deep
service_tier = "default"

[projects.'d:\\\\work']
trust_level = "trusted"
model = "not-top-level"
"""


def _env(tmp_path):
    codex = tmp_path / "codex"
    codex.mkdir()
    (codex / "config.toml").write_text(CODEX, encoding="utf-8")
    return ClientEnv(home=tmp_path, codex_home=codex, claude_dir=tmp_path / "claude",
                     claude_global_json=tmp_path / ".claude.json")


def test_enable_and_disable_restore_codex_defaults(home, tmp_path):
    env = _env(tmp_path)
    cfg = env.codex_config
    dry = routing.enable(home, codex_default=True, dry_run=True, env=env)
    assert "dry run" in dry and cfg.read_text("utf-8") == CODEX and not home.config_file.exists()
    routing.enable(home, codex_default=True, env=env)
    text = cfg.read_text("utf-8")
    assert routing.top_level(text) == {"model": '"gpt-6-luna"', "model_reasoning_effort": '"medium"'}
    assert 'service_tier = "default"' in text and 'model = "not-top-level"' in text and "# my config" in text
    assert yaml.safe_load(home.config_file.read_text("utf-8"))["routing"]["enabled"] is True
    assert list(home.backups.glob("codex-config.routing.*.toml"))
    assert "routing: on" in routing.status(home, env=env)
    routing.disable(home)
    back = routing.top_level(cfg.read_text("utf-8"))
    assert back == {"model": '"gpt-6-sol"', "model_reasoning_effort": '"high"'}
    assert yaml.safe_load(home.config_file.read_text("utf-8"))["routing"]["enabled"] is False
    assert routing.status(home, env=env).startswith("routing: off")


def test_disable_keeps_a_model_the_user_changed_since(home, tmp_path):
    env = _env(tmp_path)
    routing.enable(home, codex_default=True, env=env)
    cfg = env.codex_config
    cfg.write_text(routing.set_top_level(cfg.read_text("utf-8"), {"model": '"gpt-6-astra"'}), encoding="utf-8")
    routing.disable(home)
    assert routing.top_level(cfg.read_text("utf-8")) == {"model": '"gpt-6-astra"', "model_reasoning_effort": '"high"'}


def test_enable_without_codex_default_touches_only_arbiter(home, tmp_path):
    env = _env(tmp_path)
    routing.enable(home, env=env)
    assert env.codex_config.read_text("utf-8") == CODEX
    assert yaml.safe_load(home.config_file.read_text("utf-8"))["routing"]["enabled"] is True


def test_effort_option(home, tmp_path):
    env = _env(tmp_path)
    routing.enable(home, codex_effort="low", env=env)
    now = routing.top_level(env.codex_config.read_text("utf-8"))
    assert now == {"model": '"gpt-6-sol"', "model_reasoning_effort": '"low"'}
    assert yaml.safe_load(home.config_file.read_text("utf-8"))["routing"]["escalate"] == "effort"
    routing.disable(home)
    assert routing.top_level(env.codex_config.read_text("utf-8")) == {"model": '"gpt-6-sol"',
                                                                      "model_reasoning_effort": '"high"'}
