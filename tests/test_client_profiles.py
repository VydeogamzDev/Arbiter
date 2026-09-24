"""M5: recorded-fixture contract tests for every client profile (fixtures/clients/<id>.v<N>.yaml).

Drift detection: a profile whose version, entry shape, client names or hook translation no longer
matches its recorded fixture fails here. All client homes are sandboxed (ARBITER_CLIENT_HOME).
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path

import pytest
import yaml

from arbiter_agent.clients import mcp_entry, text_config
from arbiter_agent.clients.client_env import current_env
from arbiter_agent.clients.event_normalizer import normalize_hook
from arbiter_agent.clients.hook_dialects import DIALECTS, adapt_inbound, adapt_outbound
from arbiter_agent.clients.registry import load_registry
from arbiter_agent.daemon.clients_runtime import client_from_info
from arbiter_agent.integration import conformance
from arbiter_agent.setup.setup import SetupOptions, run_setup
from arbiter_agent.setup.uninstall import uninstall

FIX = Path(__file__).parent / "fixtures" / "clients"
REG = load_registry()
M5 = sorted(pid for pid in REG.profiles if pid not in ("codex", "claude_code", "generic_mcp"))


def fixture(pid: str) -> dict:
    p = REG.get(pid)
    return yaml.safe_load((FIX / f"{pid}.v{p.profile_version}.yaml").read_text(encoding="utf-8"))


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("ARBITER_CLIENT_HOME", str(tmp_path / "clienthome"))
    (tmp_path / "clienthome").mkdir()
    return current_env()


def setup(home, pid):
    out = io.StringIO()
    rc = run_setup(home, SetupOptions(yes=True, start_daemon=False, clients=[pid], out=out))
    return rc, out.getvalue()


def _load(fmt: str, raw: bytes | None, path: Path) -> dict:
    if not raw or not raw.strip():
        return {}
    if fmt == "yaml_named_entry":
        return text_config._yaml_load(raw, path)
    if fmt == "jsonc_named_entry":
        return text_config.load_jsonc(raw, path)
    return json.loads(raw.decode("utf-8-sig"))


def _without_ours(data: dict, keys: list[str], name: str) -> dict:
    data = json.loads(json.dumps(data))
    box = data
    for k in keys:
        box = box.get(k, {}) if isinstance(box, dict) else {}
    if isinstance(box, dict):
        box.pop(name, None)

    def prune(d):
        if isinstance(d, dict):
            return {k: prune(v) for k, v in d.items() if prune(v) != {} or k not in keys}
        return d

    return prune(data)


# ------------------------------------------------------------------ drift checks
@pytest.mark.parametrize("pid", M5)
def test_profile_has_a_fixture_for_its_version(pid):
    p = REG.get(pid)
    assert (FIX / f"{pid}.v{p.profile_version}.yaml").is_file(), "profile changed without new recorded fixtures"


@pytest.mark.parametrize("pid", M5)
def test_entry_shape_and_client_names_match_fixture(pid):
    p, f = REG.get(pid), fixture(pid)
    entry = mcp_entry.render(p.mcp.get("entry") or mcp_entry.DEFAULT_TEMPLATE, ["C:/tools/arbiter.exe", "mcp"])
    assert sorted(entry) == sorted(f["entry_keys"])
    assert mcp_entry.is_ours(entry)
    for name in f["client_names"]:
        assert client_from_info(name) == pid, name


def test_claude_desktop_is_not_claude_code():
    assert client_from_info("claude-ai") == "claude_desktop"
    assert client_from_info("claude-code") == "claude_code"


# ------------------------------------------------------------------ setup / uninstall matrix
CASES = [(pid, case) for pid in M5 for case in fixture(pid)["configs"]]


@pytest.mark.parametrize(("pid", "case"), CASES, ids=[f"{p}-{c}" for p, c in CASES])
def test_setup_rerun_uninstall_matrix(home, sandbox, pid, case):
    p, f = REG.get(pid), fixture(pid)
    target = p.expand(p.mcp.get("file"), sandbox)
    if target is None:
        pytest.skip(f"{pid} has no config file on this platform")
    raw = f["configs"][case]
    before = raw.encode("utf-8") if raw else None
    if before is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(before)
    hooks_target = p.expand((p.hooks or {}).get("file"), sandbox) if p.hooks else None
    hooks_before = hooks_target.read_bytes() if hooks_target and hooks_target.exists() else None

    rc, out = setup(home, pid)
    assert rc == 0, out
    assert conformance.check_mcp(p, sandbox).ok, out
    fmt, keys, name = p.mcp["format"], mcp_entry.container_path(p.mcp), str(p.mcp.get("name", "arbiter"))
    after = target.read_bytes()
    compare = after
    if hooks_target == target:                            # hooks share the file (Gemini): strip them too
        compare = DIALECTS[pid].remove(after, target)
    assert _without_ours(_load(fmt, compare, target), keys, name) == _without_ours(_load(fmt, before, target), keys,
                                                                                    name)
    for line in (raw or "").splitlines():                 # comments survive
        if line.strip().startswith(("//", "#")):
            assert line.strip().encode() in after, line
    if p.hooks:
        assert conformance.check_hooks(p, sandbox).ok

    rc, out = setup(home, pid)                            # re-run: nothing changes
    assert rc == 0 and target.read_bytes() == after, out

    results = uninstall(home, [pid])
    assert not any(r.startswith("FAILED") for r in results), results
    if before is None:
        assert not target.exists() or not _load(fmt, target.read_bytes(), target)
    else:
        assert target.read_bytes() == before, results
    if hooks_target is not None and hooks_target != target:
        assert (hooks_target.read_bytes() if hooks_target.exists() else None) == hooks_before


@pytest.mark.parametrize("pid", M5)
def test_user_edits_after_setup_survive_uninstall(home, sandbox, pid):
    p = REG.get(pid)
    target = p.expand(p.mcp.get("file"), sandbox)
    if target is None:
        pytest.skip("no config file on this platform")
    assert setup(home, pid)[0] == 0
    fmt = p.mcp["format"]
    if fmt == "yaml_named_entry":
        target.write_bytes(target.read_bytes() + b"user_added: 1\n")
    else:
        text = target.read_bytes().decode("utf-8")
        target.write_text(text.replace("{", '{\n  "user_added": 1,', 1), encoding="utf-8")
    time.sleep(0.01)
    results = uninstall(home, [pid])
    assert not any(r.startswith("FAILED") for r in results), results
    data = _load(fmt, target.read_bytes(), target)
    assert data.get("user_added") == 1
    assert not conformance.check_mcp(p, sandbox).ok


def test_shared_file_mcp_and_hooks_restore_byte_identical(home, sandbox):
    """Gemini keeps MCP servers and hooks in one settings.json: two manifest records, one file."""
    p = REG.get("gemini_cli")
    target = p.expand(p.mcp["file"], sandbox)
    target.parent.mkdir(parents=True)
    original = b'{\n  "theme": "Default"\n}\n'
    target.write_bytes(original)
    assert setup(home, "gemini_cli")[0] == 0
    data = json.loads(target.read_text())
    assert "arbiter" in data["mcpServers"] and set(data["hooks"]) == set(DIALECTS["gemini_cli"].events)
    uninstall(home, ["gemini_cli"])
    assert target.read_bytes() == original


# ------------------------------------------------------------------ hook dialects
INBOUND = [(pid, i) for pid in DIALECTS for i in range(len(fixture(pid).get("inbound", [])))]


@pytest.mark.parametrize(("pid", "i"), INBOUND, ids=[f"{p}-{i}" for p, i in INBOUND])
def test_hook_payload_translation(pid, i):
    case = fixture(pid)["inbound"][i]
    payload, event = adapt_inbound(pid, case["payload"], case["native"])
    ev = normalize_hook(pid, payload, surface="hook", keyer=lambda b: "k", event_hint=event)
    exp = case["expect"]
    assert ev.event_type == exp["event_type"]
    assert ev.native_session_id == exp["session"]
    for key in ("cwd", "prompt", "last_assistant_message", "tool_response"):
        if key in exp:
            assert payload.get(key) == exp[key], key


OUTBOUND = [(pid, i) for pid in DIALECTS for i in range(len(fixture(pid).get("outbound", [])))]


@pytest.mark.parametrize(("pid", "i"), OUTBOUND, ids=[f"{p}-{i}" for p, i in OUTBOUND])
def test_hook_response_translation(pid, i):
    case = fixture(pid)["outbound"][i]
    assert adapt_outbound(pid, case["native"], case["response"]) == case["expect"]


def test_dialect_hooks_through_the_daemon(home, tmp_path):
    """Gemini can hold back a turn end (deny -> retry); Cursor can't, so it stays annotate-only."""
    from arbiter_agent.config.loader import build_config
    from arbiter_agent.daemon.server import Daemon

    d = Daemon(home, config=build_config({"completion": {"gate_mode": "block"}})).start()
    try:
        for client, start, stop, sfield, mfield in (
                ("gemini_cli", "BeforeAgent", "AfterAgent", "session_id", "prompt_response"),
                ("cursor", "beforeSubmitPrompt", "stop", "conversation_id", None)):
            base = {sfield: f"{client}-s", "cwd": str(tmp_path)} if client != "cursor" else {
                sfield: f"{client}-s", "workspace_roots": [str(tmp_path)]}
            d.ingest_hook(client, {**base, "hook_event_name": start, "prompt": "Fix the addition bug in calc.py."},
                          surface="hook")
            if client == "cursor":
                d.ingest_hook(client, {**base, "hook_event_name": "afterAgentResponse", "text": "Done."},
                              surface="hook")
            stop_payload = {**base, "hook_event_name": stop}
            if mfield:
                stop_payload[mfield] = "Done."
            out = d.ingest_hook(client, stop_payload, surface="hook")
            if client == "gemini_cli":
                assert out["response"].get("decision") == "deny", out
                assert out["response"]["reason"].startswith("[Arbiter]")
            else:
                assert out["response"] == {}, out
        rows = d.engine.sessions()
        assert {r["session_id"] for r in rows} >= {"gemini_cli:gemini_cli-s", "cursor:cursor-s"}
    finally:
        d.shutdown()


def test_probe_verifies_t1_from_client_name(home, sandbox):
    from arbiter_agent.daemon.server import Daemon

    assert setup(home, "zed")[0] == 0
    d = Daemon(home).start()
    try:
        d.clients.client_seen({"name": "Zed", "version": "0.200", "cwd": str(sandbox.home)})
        caps = {c["client"]: c for c in d.clients.run_probe()}
        assert "T1" in caps["zed"]["verified"], caps.get("zed")
    finally:
        d.shutdown()
