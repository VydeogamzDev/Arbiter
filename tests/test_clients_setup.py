"""M2: setup / uninstall fixture matrix, parsers, conformance. All client homes are temporary."""

import io
import json
import os
import tomllib
from pathlib import Path

import pytest

from arbiter_agent.clients import config_merge as cm
from arbiter_agent.clients.client_env import current_env
from arbiter_agent.clients.watchers.parsers import PARSERS
from arbiter_agent.setup.manifest import Manifest
from arbiter_agent.setup.setup import SetupOptions, run_setup
from arbiter_agent.setup.uninstall import uninstall

FIX = Path(__file__).parent / "fixtures"

CODEX_CONFIG_TYPICAL = b'''model = "gpt-6-astra"
approval_policy = "never"
sandbox_mode = "workspace-write"
notify = ["python", "notify.py"]

[mcp_servers.eveos]
command = "node"
args = ["eveos.js"]

[projects.'c:\\users\\me\\repo']
trust_level = "trusted"

[hooks.state.'C:\\other\\hooks.json:stop:0:0']
trusted_hash = "sha256:abc"
'''
CODEX_HOOKS_USER = json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "notify.exe"}]}]}},
                              indent=2).encode() + b"\n"
CLAUDE_JSON_TYPICAL = json.dumps({"numStartups": 12, "mcpServers": {"higgsfield": {"type": "http", "url": "https://x"}},
                                  "projects": {"C:/work": {"allowedTools": []}}}, indent=2).encode()
CLAUDE_SETTINGS_TYPICAL = json.dumps({"model": "opus", "permissions": {"allow": ["Bash(git status)"]},
                                      "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]},
                                      "enabledPlugins": {"x@y": True}}, indent=2).encode() + b"\n"

CODEX_STATES = {
    "missing": None,
    "empty": b"",
    "typical": CODEX_CONFIG_TYPICAL,
}
HOOKS_STATES = {"missing": None, "user_hooks": CODEX_HOOKS_USER, "empty_obj": b"{}\n"}
CLAUDE_JSON_STATES = {"missing": None, "typical": CLAUDE_JSON_TYPICAL}
CLAUDE_SETTINGS_STATES = {"missing": None, "typical": CLAUDE_SETTINGS_TYPICAL, "no_newline": b'{"theme":"dark"}'}


@pytest.fixture
def clients_home(tmp_path, monkeypatch):
    codex = tmp_path / "codex_home"
    claude = tmp_path / "claude_dir"
    codex.mkdir()
    claude.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    monkeypatch.setenv("ARBITER_CLIENT_HOME", str(tmp_path))
    return current_env()


def write(path: Path, data):
    if data is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def setup(home, clients, **kw):
    out = io.StringIO()
    rc = run_setup(home, SetupOptions(yes=True, start_daemon=False, clients=clients, out=out, **kw))
    return rc, out.getvalue()


@pytest.mark.parametrize("cfg_state", CODEX_STATES)
@pytest.mark.parametrize("hooks_state", HOOKS_STATES)
def test_codex_matrix_setup_rerun_uninstall(home, clients_home, cfg_state, hooks_state):
    env = clients_home
    write(env.codex_config, CODEX_STATES[cfg_state])
    write(env.codex_hooks, HOOKS_STATES[hooks_state])
    before_cfg, before_hooks = cm.read_bytes(env.codex_config), cm.read_bytes(env.codex_hooks)

    rc, out = setup(home, ["codex"])
    assert rc == 0, out
    cfg = tomllib.loads(env.codex_config.read_text())
    assert cfg["mcp_servers"]["arbiter"]["args"][-1] == "mcp"
    hooks = json.loads(env.codex_hooks.read_text())["hooks"]
    assert all(any(h.get("server") == "arbiter" for g in hooks[e] for h in g["hooks"]) for e in
               ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SessionEnd"))
    if cfg_state == "typical":  # user settings preserved, permissions/sandbox untouched, no trust written
        assert cfg["approval_policy"] == "never" and cfg["sandbox_mode"] == "workspace-write"
        assert cfg["mcp_servers"]["eveos"]["command"] == "node"
        assert list(cfg["hooks"]["state"]) == ["C:\\other\\hooks.json:stop:0:0"]
    if hooks_state == "user_hooks":
        assert hooks["Stop"][0]["hooks"][0]["command"] == "notify.exe"  # user's group stays first
    assert "trust" in out.lower()  # the one-time trust step is explained

    snap_cfg, snap_hooks = env.codex_config.read_bytes(), env.codex_hooks.read_bytes()
    rc, out = setup(home, ["codex"])  # idempotent
    assert env.codex_config.read_bytes() == snap_cfg and env.codex_hooks.read_bytes() == snap_hooks
    assert "unchanged" in out

    for line in uninstall(home):
        assert not line.startswith("FAILED"), line
    assert cm.read_bytes(env.codex_config) == before_cfg  # byte-identical (or gone again)
    assert cm.read_bytes(env.codex_hooks) == before_hooks


@pytest.mark.parametrize("json_state", CLAUDE_JSON_STATES)
@pytest.mark.parametrize("settings_state", CLAUDE_SETTINGS_STATES)
def test_claude_matrix_setup_rerun_uninstall(home, clients_home, json_state, settings_state):
    env = clients_home
    write(env.claude_global_json, CLAUDE_JSON_STATES[json_state])
    write(env.claude_settings, CLAUDE_SETTINGS_STATES[settings_state])
    before_json, before_settings = cm.read_bytes(env.claude_global_json), cm.read_bytes(env.claude_settings)

    rc, out = setup(home, ["claude_code"])
    assert rc == 0, out
    gj = json.loads(env.claude_global_json.read_text())
    assert gj["mcpServers"]["arbiter"]["type"] == "stdio"
    st = json.loads(env.claude_settings.read_text())
    stop_handlers = [h for g in st["hooks"]["Stop"] for h in g["hooks"]]
    ours = [h for h in stop_handlers if h.get("type") == "http"]
    assert len(ours) == 1 and "/hook/claude_code/Stop" in ours[0]["url"]
    token = (home.hook_token_file).read_text().strip()
    assert token not in out  # diffs mask the hook token
    if json_state == "typical":
        assert gj["mcpServers"]["higgsfield"]["url"] == "https://x" and gj["numStartups"] == 12
    if settings_state == "typical":
        assert st["permissions"] == {"allow": ["Bash(git status)"]}  # permissions untouched
        assert st["model"] == "opus" and stop_handlers[0]["command"] == "say done"

    snap = (env.claude_global_json.read_bytes(), env.claude_settings.read_bytes())
    setup(home, ["claude_code"])
    assert (env.claude_global_json.read_bytes(), env.claude_settings.read_bytes()) == snap

    for line in uninstall(home):
        assert not line.startswith("FAILED"), line
    assert cm.read_bytes(env.claude_global_json) == before_json
    assert cm.read_bytes(env.claude_settings) == before_settings


@pytest.mark.parametrize("path_attr, content", [
    ("codex_config", b"this is = = not toml ["),
    ("codex_config", b"[mcp_servers.arbiter]\ncommand = 'mine'\n"),       # user-owned conflict
    ("codex_config", b"mcp_servers = { other = { command = 'x' } }\n"),   # inline table can't be extended
    ("codex_hooks", b"{not json"),
    ("codex_hooks", b'{"hooks": []}'),
])
def test_unsafe_codex_files_left_untouched(home, clients_home, path_attr, content):
    path = getattr(clients_home, path_attr)
    write(path, content)
    _rc, out = setup(home, ["codex"])
    assert path.read_bytes() == content  # zero corrupted configs
    assert "!!" in out and "skipped" in out


def test_invalid_claude_json_left_untouched(home, clients_home):
    write(clients_home.claude_global_json, b'{"broken": ')
    _rc, out = setup(home, ["claude_code"])
    assert clients_home.claude_global_json.read_bytes() == b'{"broken": '
    assert "skipped" in out


def test_claude_live_file_rewritten_between_setup_and_uninstall(home, clients_home):
    env = clients_home
    write(env.claude_global_json, CLAUDE_JSON_TYPICAL)
    setup(home, ["claude_code"])
    data = json.loads(env.claude_global_json.read_text())
    data["numStartups"] = 13  # Claude Code rewrites its own file
    data["newClientKey"] = {"a": 1}
    env.claude_global_json.write_text(json.dumps(data))
    lines = uninstall(home)
    assert any("kept them" in ln for ln in lines)
    after = json.loads(env.claude_global_json.read_text())
    assert "arbiter" not in after["mcpServers"] and after["newClientKey"] == {"a": 1} and after["numStartups"] == 13


def test_user_edits_to_toml_survive_uninstall(home, clients_home):
    env = clients_home
    write(env.codex_config, CODEX_CONFIG_TYPICAL)
    setup(home, ["codex"])
    env.codex_config.write_bytes(env.codex_config.read_bytes().replace(b'model = "gpt-6-astra"', b'model = "luna"'))
    uninstall(home, ["codex"])
    text = env.codex_config.read_text()
    assert 'model = "luna"' in text and "arbiter" not in text
    tomllib.loads(text)


def test_manifest_keeps_original_backup_across_reruns(home, clients_home):
    env = clients_home
    write(env.claude_settings, CLAUDE_SETTINGS_TYPICAL)
    setup(home, ["claude_code"])
    first = Manifest.load(home).find("claude_code", str(env.claude_settings)).backup
    home.hook_token_file.write_text("rotated-token-xyz")  # forces a real rewrite on the next setup
    setup(home, ["claude_code"])
    rec = Manifest.load(home).find("claude_code", str(env.claude_settings))
    assert rec.backup == first and Path(first).read_bytes() == CLAUDE_SETTINGS_TYPICAL
    uninstall(home)
    assert env.claude_settings.read_bytes() == CLAUDE_SETTINGS_TYPICAL


def test_dry_run_and_print(home, clients_home, capsys):
    rc, out = setup(home, ["codex", "claude_code"], dry_run=True)
    assert rc == 0 and "dry run" in out
    assert not clients_home.codex_config.exists() and not clients_home.claude_settings.exists()
    from arbiter_agent.clients.registry import load_registry
    from arbiter_agent.setup.setup import print_snippet

    buf = io.StringIO()
    print_snippet(load_registry().get("generic_mcp"), buf)
    assert '"mcpServers"' in buf.getvalue() and "[mcp_servers.arbiter]" in buf.getvalue()


def test_codex_hook_templates_only_use_guaranteed_fields():
    from arbiter_agent.clients.codex import hooks

    for event, fields in hooks.EVENT_FIELDS.items():
        inp = hooks.handler(event)["input"]
        assert inp["client"] == "codex"
        if not event.startswith("Subagent"):
            assert "agent_id" not in inp and "agent_type" not in inp  # decision 0015
    assert json.dumps(hooks.hook_groups(), sort_keys=True) == json.dumps(hooks.hook_groups(), sort_keys=True)
    assert "arbiter.exe" not in json.dumps(hooks.hook_groups())  # hash-stable: no paths/versions


def test_codex_rollout_parser():
    _version, parse = PARSERS["codex_rollout_v1"]
    ctx: dict = {}
    recs = [r for line in (FIX / "codex_rollout.jsonl").read_text().splitlines() for r in parse(line, ctx)]
    kinds = [r.record_type for r in recs]
    assert kinds[0] == "session_meta" and ctx["session_id"] == "01codexsess" and ctx["cwd"] == "C:/work/proj"
    assert len(recs[0].payload.get("base_instructions", "")) == 0  # system prompt not stored
    tool = next(r for r in recs if r.record_type == "tool_result" and r.tool_use_id == "call_A")
    assert tool.attrs["exit_code"] == 1 and "1 failed" in tool.payload["stdout"]
    usage = next(r for r in recs if r.record_type == "usage")
    assert usage.attrs["cached_input_tokens"] == 1000 and usage.attrs["rate_limit_used_percent"] == 42.0
    settings = next(r for r in recs if r.record_type == "settings")
    assert settings.attrs["reasoning_effort"] == "high"
    users = [r for r in recs if r.record_type == "user_message"]
    assert users[0].attrs.get("injected") and not users[1].attrs.get("injected")
    exec_result = next(r for r in recs if r.tool_use_id == "exec_1")
    assert exec_result.record_type == "tool_result"
    assert all(r.session_id == "01codexsess" for r in recs)


def test_claude_parser():
    _, parse = PARSERS["claude_code_v1"]
    ctx: dict = {}
    recs, errors = [], 0
    for line in (FIX / "claude_session.jsonl").read_text().splitlines():
        try:
            recs += parse(line, ctx)
        except ValueError:
            errors += 1
    assert errors == 1  # the deliberately broken line
    kinds = [r.record_type for r in recs]
    assert kinds.count("user_message") == 1  # isMeta skipped
    tr = next(r for r in recs if r.record_type == "tool_result")
    assert tr.tool_use_id == "toolu_1" and tr.payload["stdout"].startswith("4 passed")
    assert any(r.record_type == "tool_call" and r.attrs["tool_name"] == "PowerShell" for r in recs)
    assert next(r for r in recs if r.record_type == "usage").attrs["cache_read_input_tokens"] == 18000
    assert {r.session_id for r in recs} == {"csess"}


def test_conformance_and_probe_without_daemon(home, clients_home):
    from arbiter_agent.clients.registry import load_registry
    from arbiter_agent.integration import capability_probe

    reg = load_registry()
    setup(home, ["codex"])
    cap = capability_probe.probe(reg.get("codex"), clients_home, None)
    assert cap.configured == {"T1", "T2"}  # no transcripts yet
    assert cap.verified == set()  # nothing observed yet: configured is not verified
    assert any("trust" in n for n in cap.notes)
    # drift: the user removes Arbiter's hooks by hand -> T2 dropped, T1 kept
    clients_home.codex_hooks.write_text('{"hooks": {}}')
    cap2 = capability_probe.probe(reg.get("codex"), clients_home, None, previous={"T1", "T2"})
    assert "T2" not in cap2.configured and any("T2 dropped" in d for d in cap2.drift)


def test_setup_skips_claude_when_plugin_enabled(home, clients_home):
    env = clients_home
    settings = json.dumps({"enabledPlugins": {"arbiter@arbiter-local": True}}).encode()
    write(env.claude_settings, settings)
    rc, out = setup(home, ["claude_code"])
    assert rc == 0 and "plugin (arbiter@arbiter-local) is enabled" in out
    assert env.claude_settings.read_bytes() == settings and not env.claude_global_json.exists()


def test_claude_plugin_package_is_consistent():
    root = Path(__file__).parent.parent / "packaging" / "claude-code"
    market = json.loads((root / ".claude-plugin" / "marketplace.json").read_text())
    plugin_dir = root / market["plugins"][0]["source"]
    manifest = json.loads((plugin_dir / ".claude-plugin" / "plugin.json").read_text())
    mcp = json.loads((plugin_dir / ".mcp.json").read_text())
    hooks = json.loads((plugin_dir / "hooks" / "hooks.json").read_text())["hooks"]
    from arbiter_agent import __version__
    from arbiter_agent.clients.claude_code.hooks import EVENTS

    assert manifest["name"] == market["plugins"][0]["name"] == "arbiter"
    assert manifest["version"] == __version__
    assert mcp["mcpServers"]["arbiter"]["args"] == ["mcp"]
    assert sorted(hooks) == sorted(EVENTS)
    for ev, groups in hooks.items():
        h = groups[0]["hooks"][0]
        assert h["type"] == "command" and h["args"] == ["hook", "claude_code", ev]   # exec form, no shell


def test_setup_delegates_appdata_configs_outside_packaged_app(home, clients_home, monkeypatch):
    """Inside an MSIX-packaged app (e.g. the Claude desktop app) new AppData files are redirected into
    a private copy the real client never reads. Setup edits the unaffected clients itself and re-runs
    itself outside the package (WMI) for the rest, so an agent can set Arbiter up without the user
    opening a terminal."""
    from arbiter_agent import appcontainer

    monkeypatch.setenv("ARBITER_TEST_PACKAGED", "Claude_test")
    monkeypatch.setattr(appcontainer, "_dir_redirected", lambda d, pkg: True)   # as a redirected AppData dir
    monkeypatch.setenv("LOCALAPPDATA", str(clients_home.localappdata))
    monkeypatch.setenv("APPDATA", str(clients_home.appdata))
    Path(clients_home.appdata).mkdir(parents=True, exist_ok=True)
    calls = []

    def fake_outside(argv, log_dir, timeout=180.0):
        calls.append(argv)
        return 0, "Planned changes:\n* register the Arbiter MCP server (servers.arbiter)\nDone."

    monkeypatch.setattr(appcontainer, "run_outside", fake_outside)
    rc, out = setup(home, ["vscode", "claude_code"])
    assert rc == 0 and "runs outside the package" in out and "  | * register the Arbiter MCP server" in out
    assert len(calls) == 1
    argv = calls[0]
    assert argv[argv.index("--clients") + 1] == "vscode" and "--yes" in argv and "--home" in argv
    assert not (Path(clients_home.appdata) / "Code" / "User" / "mcp.json").exists()   # not written from inside
    import json as _json

    claude = _json.loads((Path(os.environ["CLAUDE_CONFIG_DIR"]) / ".claude.json").read_text(encoding="utf-8"))
    assert "arbiter" in claude["mcpServers"]                    # ~/.claude isn't virtualized: written here

    calls.clear()
    rc, out = setup(home, ["vscode"], dry_run=True)
    assert rc == 0 and "--dry-run" in calls[0]
    monkeypatch.setattr(appcontainer, "run_outside", lambda *a, **k: (1, "error: boom"))
    rc, out = setup(home, ["vscode"])
    assert rc == 3 and "error: boom" in out


def test_setup_output_file_mode_writes_exit_line(home, clients_home, tmp_path):
    """The WMI-started half of run_outside() reports through --output-file."""
    from arbiter_agent import appcontainer
    from arbiter_agent.cli import main

    out = tmp_path / "out.log"
    rc = main(["--home", str(home.root), "setup", "--clients", "claude_code", "--dry-run", "--no-start",
               "--output-file", str(out)])
    text = out.read_text(encoding="utf-8")
    assert rc == 0 and "Planned changes" in text and text.rstrip().endswith(f"{appcontainer.EXIT_MARK}0")


def test_stable_port_avoids_ephemeral_ranges():
    from arbiter_agent.daemon.server import STABLE_PORT_RANGE, free_stable_port

    p1, p2 = free_stable_port("install-a"), free_stable_port("install-a")
    assert STABLE_PORT_RANGE[0] <= p1 < STABLE_PORT_RANGE[1] and p1 == p2    # deterministic per install
