"""M10: tool gateway: catalog, schema validation, search, benchmark/policy, approval preservation,
adoption/release through the setup machinery, the MCP shim in gateway mode, and bounded auto-context."""

from __future__ import annotations

import io
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from arbiter_agent.clients.client_env import current_env
from arbiter_agent.clients.registry import load_registry
from arbiter_agent.eval.trace_replay import Harness
from arbiter_agent.gateway import adopt, benchmark, search
from arbiter_agent.gateway.authorization_bridge import AuthorizationBridge
from arbiter_agent.gateway.call import Gateway
from arbiter_agent.gateway.catalog import Catalog, from_server, schema_hash
from arbiter_agent.gateway.registry import Adopted, Registry
from arbiter_agent.gateway.schema_validation import validate
from arbiter_agent.gateway.upstream import LaunchSpec, Upstream, UpstreamError

FAKE = str(Path(__file__).parent / "fixtures" / "fake_mcp_server.py")


def fake_entry(**env: str) -> dict[str, Any]:
    return {"command": sys.executable, "args": [FAKE], "env": {"FAKE_TOKEN": "t0k", **env}}


# ------------------------------------------------------------------ schema validation + catalog
def test_schema_validation():
    s = {"type": "object", "properties": {"n": {"type": "integer", "minimum": 1}, "tags": {"type": "array",
         "items": {"type": "string"}, "maxItems": 2}, "mode": {"enum": ["a", "b"]}}, "required": ["n"],
         "additionalProperties": False}
    assert validate({"n": 2, "tags": ["x"], "mode": "a"}, s) == []
    errs = validate({"n": 0, "tags": ["x", 1, "y"], "mode": "c", "zzz": 1}, s)
    assert any("$.n" in e for e in errs) and any("$.tags[1]" in e for e in errs)
    assert any("more than 2" in e for e in errs) and any("zzz" in e for e in errs) and any("mode" in e for e in errs)
    assert validate({}, s) == ["$.n: required"]
    assert validate(True, {"type": "integer"})                          # bools aren't integers


def test_read_only_needs_annotation_and_confirmation():
    tools = [{"name": "r", "description": "read", "annotations": {"readOnlyHint": True}, "inputSchema": {}},
             {"name": "w", "description": "write", "annotations": {}, "inputSchema": {}}]
    unconfirmed = {t.name: t.read_only for t in from_server("s", tools)}
    confirmed = {t.name: t.read_only for t in from_server("s", tools, {"r": schema_hash(tools[0]),
                                                                       "w": schema_hash(tools[1])})}
    assert unconfirmed == {"r": False, "w": False} and confirmed == {"r": True, "w": False}
    changed = dict(tools[0], description="read, and now also deletes")
    assert not from_server("s", [changed], {"r": schema_hash(tools[0])})[0].read_only   # schema changed


# ------------------------------------------------------------------ search + benchmark
def test_search_family_is_a_preference_and_returns_best_schema():
    cat, _ = benchmark.reference()
    res = search.search(cat, "what releases exist for this repo")
    assert res["results"][0]["tool_id"] == "github.list_releases" and res["best"]["tool_id"] == "github.list_releases"
    floor = search.search(cat, "zzzz qqqq")                           # nothing matches: the recall floor still answers
    assert len(floor["results"]) == search.MIN_RESULTS


def test_benchmark_policy_decisions():
    small, tasks = benchmark.reference([])
    big, _ = benchmark.reference()
    core = benchmark.core_defs()
    assert not benchmark.decide("x", small, core, native_deferral=False, tasks=tasks).enabled   # too small
    on = benchmark.decide("x", big, core, native_deferral=False, measured_recall=0.95)
    assert on.enabled and on.savings >= benchmark.MATERIAL
    lexical_only = benchmark.decide("x", big, core, native_deferral=False, measured_recall=0.85)
    assert not lexical_only.enabled and "recall" in " ".join(lexical_only.reasons)
    mid, _ = benchmark.reference(["github", "filesystem"])
    assert not benchmark.decide("x", mid, core, native_deferral=True, measured_recall=0.95).enabled
    assert benchmark.decide("x", small, core, native_deferral=True, mode="on").enabled             # forced
    assert benchmark.break_even_schema_tokens(big, core, benchmark.SessionModel()) > 0


def test_heldout_search_recall_is_recorded_honestly():
    q = benchmark.search_quality()
    assert q["mode"] == "lexical" and q["dev_recall"] >= 0.95
    assert q["heldout_recall"] < benchmark.MIN_RECALL      # lexical alone doesn't generalize: gateway stays off


# ------------------------------------------------------------------ approval preservation
def _gateway(elicitation: bool, answers: list[Any] | None = None) -> tuple[Gateway, list[Any], list[str]]:
    tools = [
        {"name": "read_note", "description": "Read a note.", "annotations": {"readOnlyHint": True},
         "inputSchema": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}},
        {"name": "write_note", "description": "Write a note.", "annotations": {},
         "inputSchema": {"type": "object", "properties": {"key": {"type": "string"}, "text": {"type": "string"}},
                         "required": ["key", "text"]}},
        {"name": "delete_all", "description": "Delete all.", "annotations": {"destructiveHint": True},
         "inputSchema": {"type": "object", "properties": {}}},
    ]
    cat = Catalog(from_server("notes", tools, {"read_note": schema_hash(tools[0])}))
    asked: list[Any] = []
    ran: list[str] = []
    answers = list(answers or [])

    def elicit(req):
        asked.append(req)
        return answers.pop(0) if answers else {"action": "decline"}

    gw = Gateway(cat, AuthorizationBridge(elicitation), local_call=lambda n, a: {"content": []},
                 upstream_call=lambda s, t, a: ran.append(t) or {"content": [{"type": "text", "text": "ok"}]},
                 elicit=elicit if elicitation else None)
    return gw, asked, ran


def _err(res: dict[str, Any]) -> str | None:
    if not res.get("isError"):
        return None
    return json.loads(res["content"][0]["text"])["error"]["code"]


def test_read_only_runs_mutating_denied_without_elicitation():
    gw, asked, ran = _gateway(elicitation=False)
    assert _err(gw.handle("tool_call", {"tool_id": "notes.read_note", "arguments": {"key": "a"}})) is None
    assert _err(gw.handle("tool_call", {"tool_id": "notes.write_note", "arguments": {"key": "a", "text": "x"}})) \
        == "denied"
    assert ran == ["read_note"] and asked == []


def test_mirrored_approval_per_call_no_inheritance_sticky_denials():
    yes = {"action": "accept", "content": {"approve": True}}
    gw, asked, ran = _gateway(elicitation=True, answers=[yes, {"action": "decline"}, yes])
    w = {"tool_id": "notes.write_note", "arguments": {"key": "a", "text": "x"}}
    assert _err(gw.handle("tool_call", w)) is None                          # approved once
    assert "notes.write_note" in asked[0]["message"] and '"text": "x"' in asked[0]["message"]
    d = {"tool_id": "notes.delete_all", "arguments": {}}
    assert _err(gw.handle("tool_call", d)) == "denied"                      # approving write_note didn't approve this
    n_asked = len(asked)
    assert _err(gw.handle("tool_call", d)) == "denied" and len(asked) == n_asked   # sticky: not asked again
    assert _err(gw.handle("tool_call", w)) is None and len(asked) == n_asked + 1  # the next write asks again
    assert ran == ["write_note", "write_note"]
    malformed = _gateway(elicitation=True, answers=[{"action": "accept", "content": {"approve": "yes"}}])[0]
    assert _err(malformed.handle("tool_call", w)) == "denied"               # only a real boolean true approves


def test_structured_errors():
    gw, _, ran = _gateway(elicitation=False)
    assert _err(gw.handle("tool_call", {"tool_id": "nope.x", "arguments": {}})) == "unknown_tool"
    bad = gw.handle("tool_call", {"tool_id": "notes.read_note", "arguments": {"key": 5}})
    assert _err(bad) == "invalid_arguments" and "$.key" in bad["content"][0]["text"]
    desc = json.loads(gw.handle("tool_describe", {"tool_ids": ["notes.read_note", "ghost"]})["content"][0]["text"])
    assert desc["tools"][0]["tool_id"] == "notes.read_note" and desc["unknown"] == ["ghost"]
    assert ran == []


# ------------------------------------------------------------------ upstream: no scope expansion
def test_upstream_launch_spec_is_exact_and_offers_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("ARBITER_GATEWAY_LEAK", raising=False)
    spec = LaunchSpec.from_entry({**fake_entry(), "cwd": str(tmp_path)})
    assert spec.args == [FAKE] and spec.env == {"FAKE_TOKEN": "t0k"} and spec.cwd == str(tmp_path)
    up = Upstream("notes", spec).start()
    try:
        assert {t["name"] for t in up.tools} == {"read_note", "whoami", "write_note", "delete_all"}
        who = json.loads(up.call("whoami", {})["content"][0]["text"])
        assert who == {"token": "t0k", "cwd": str(tmp_path), "extra": None}
        with pytest.raises(UpstreamError):
            up.call("nope", {})
    finally:
        up.close()
    with pytest.raises(ValueError, match="remote"):
        LaunchSpec.from_entry({"url": "https://example.com/mcp"})


# ------------------------------------------------------------------ adoption / release / uninstall
@pytest.fixture
def clients_home(tmp_path, monkeypatch):
    for d in ("codex_home", "claude_dir"):
        (tmp_path / d).mkdir()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex_home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude_dir"))
    monkeypatch.setenv("ARBITER_CLIENT_HOME", str(tmp_path))
    return current_env()


def _toml_entry(name: str, **env: str) -> str:
    e = fake_entry(**env)
    args = ", ".join(json.dumps(a) for a in e["args"])
    envs = "\n".join(f"{k} = {json.dumps(v)}" for k, v in e["env"].items())
    return f'[mcp_servers.{name}]\ncommand = {json.dumps(e["command"])}\nargs = [{args}]\n\n' \
           f'[mcp_servers.{name}.env]\n{envs}\n'


def test_adopt_refused_when_mutating_tools_and_no_elicitation(home, clients_home):
    cfg = Path(clients_home.codex_home) / "config.toml"
    cfg.write_text('model = "x"\n\n' + _toml_entry("notes"), encoding="utf-8")
    codex = load_registry().get("codex")
    plan = adopt.plan_adopt(codex, clients_home, "notes")
    assert not plan.allowed and "write_note" in plan.reason and "per-tool approvals" in plan.reason
    with pytest.raises(PermissionError):
        adopt.apply_adopt(home, codex, plan)
    assert cfg.read_text(encoding="utf-8").count("[mcp_servers.notes]") == 1       # untouched


def test_adopt_and_release_codex_read_only_server_byte_identical(home, clients_home):
    cfg = Path(clients_home.codex_home) / "config.toml"
    original = '# my settings\nmodel = "x"\n\n' + _toml_entry("notes", FAKE_READONLY="1") + \
        '\n[mcp_servers.other]\ncommand = "other"\n'
    cfg.write_text(original, encoding="utf-8")
    codex = load_registry().get("codex")
    plan = adopt.plan_adopt(codex, clients_home, "notes")
    assert plan.allowed and plan.read_only == ["read_note", "whoami"] and not plan.mutating
    adopt.apply_adopt(home, codex, plan)
    after = cfg.read_text(encoding="utf-8")
    assert "[mcp_servers.notes" not in after and "[mcp_servers.other]" in after and "# my settings" in after
    item = Registry.load(home).get("codex", "notes")
    assert item is not None and set(item.confirmed_read_only) == {"read_note", "whoami"}
    assert adopt.release(home, "codex", "notes").startswith("restored")
    import tomllib

    assert tomllib.loads(cfg.read_text(encoding="utf-8")) == tomllib.loads(original)
    assert Registry.load(home).get("codex", "notes") is None


def test_uninstall_restores_adopted_server(home, clients_home):
    from arbiter_agent.setup.uninstall import uninstall

    claude = load_registry().get("claude_code")
    loc = adopt.location(claude, clients_home)
    assert loc is not None
    claude_json = loc.path
    data = {"theme": "dark", "mcpServers": {"notes": fake_entry(FAKE_READONLY="1")}}
    claude_json.write_text(json.dumps(data), encoding="utf-8")
    plan = adopt.plan_adopt(claude, clients_home, "notes")
    assert plan.allowed
    adopt.apply_adopt(home, claude, plan)
    assert "notes" not in json.loads(claude_json.read_text(encoding="utf-8")).get("mcpServers", {})
    uninstall(home, ["claude_code"])
    assert json.loads(claude_json.read_text(encoding="utf-8")) == data
    assert Registry.load(home).for_client("claude_code") == []


def test_vscode_with_elicitation_can_adopt_mutating_servers(home, clients_home):
    vscode = load_registry().get("vscode")
    assert vscode.elicitation
    loc = adopt.location(vscode, clients_home)
    assert loc is not None
    loc.path.parent.mkdir(parents=True, exist_ok=True)
    loc.path.write_text(json.dumps({"servers": {"notes": {"type": "stdio", **fake_entry()}}}), encoding="utf-8")
    plan = adopt.plan_adopt(vscode, clients_home, "notes")
    assert plan.allowed and plan.mutating == ["delete_all", "write_note"]


# ------------------------------------------------------------------ the shim in gateway mode
class _Pipe(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[dict[str, Any]] = []
        self.cv = threading.Condition()

    def write(self, s: str) -> int:
        with self.cv:
            for ln in s.splitlines():
                if ln.strip():
                    self.lines.append(json.loads(ln))
            self.cv.notify_all()
        return len(s)

    def wait_for(self, pred: Any, timeout: float = 20.0) -> dict[str, Any]:
        end = time.monotonic() + timeout
        with self.cv:
            while True:
                for m in self.lines:
                    if pred(m):
                        return m
                left = end - time.monotonic()
                if left <= 0:
                    raise AssertionError(f"timed out; saw {self.lines[-3:]}")
                self.cv.wait(left)


def test_shim_gateway_mode_end_to_end(home, monkeypatch):
    from arbiter_agent.shims import mcp_server
    from arbiter_agent.shims.mcp_server import MCPShim

    monkeypatch.setattr(mcp_server, "trigger_launch", lambda paths: None)   # don't leave a daemon behind

    reg = Registry(home.config / "gateway" / "adopted.json")
    up = Upstream("probe", LaunchSpec.from_entry(fake_entry())).start()
    tools = up.tools
    up.close()
    reg.put(Adopted("vscode", "notes", fake_entry(),
                    {t["name"]: schema_hash(t) for t in tools if t["annotations"].get("readOnlyHint")},
                    ["delete_all", "write_note"]))
    reg.save()
    out = _Pipe()
    shim = MCPShim(home, out=out)
    shim.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {"elicitation": {}},
        "clientInfo": {"name": "Visual Studio Code", "version": "1.104"}}})
    shim.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    names = {t["name"] for t in out.wait_for(lambda m: m.get("id") == 2)["result"]["tools"]}
    assert {"tool_search", "tool_describe", "tool_call", "arbiter_hook"} <= names and "arbiter_search" not in names
    shim.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "tool_search", "arguments": {"query": "write a note"}}})
    found = json.loads(out.wait_for(lambda m: m.get("id") == 3)["result"]["content"][0]["text"])
    assert "notes.write_note" in [r["tool_id"] for r in found["results"]]
    shim.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
        "name": "tool_call", "arguments": {"tool_id": "notes.write_note", "arguments": {"key": "k", "text": "v"}}}})
    ask = out.wait_for(lambda m: m.get("method") == "elicitation/create")
    assert "notes.write_note" in ask["params"]["message"]
    shim.handle({"jsonrpc": "2.0", "id": ask["id"], "result": {"action": "accept", "content": {"approve": True}}})
    assert out.wait_for(lambda m: m.get("id") == 4)["result"]["content"][0]["text"] == "ok"
    shim.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {
        "name": "tool_call", "arguments": {"tool_id": "notes.read_note", "arguments": {"key": "k"}}}})
    assert out.wait_for(lambda m: m.get("id") == 5)["result"]["content"][0]["text"] == "v"   # no prompt for reads
    for u in shim._upstreams.values():
        u.close()


# ------------------------------------------------------------------ M10.4 bounded auto-context
def test_auto_context_only_on_new_tasks_and_within_deadline():
    calls: list[Any] = []

    def provider(cwd, ctx, budget):
        calls.append(ctx["query"])
        return {"pins": [{"path": "src/calc.py"}], "ranked": [{"path": "tests/test_calc.py"}], "k": 4}

    with Harness(config={"retrieval": {"auto_context": True, "auto_context_deadline_ms": 250}}) as h:
        h.engine.context_provider = provider
        first = h.prompt("Fix the addition bug in calc.py.")
        text = first["hookSpecificOutput"]["additionalContext"]
        assert "src/calc.py" in text and "suggestion" in text and len(text) <= 600
        assert h.prompt("also handle negatives") == {}                  # a continuation: nothing injected
        assert len(calls) == 1

        def slow(cwd, ctx, budget):
            time.sleep(budget + 0.5)
            return {"pins": [{"path": "x.py"}], "ranked": [], "k": 4}

        h.engine.context_provider = slow
        t0 = time.monotonic()
        assert h.prompt("New task: write a README.") == {}               # too slow: skipped, not waited for
        assert time.monotonic() - t0 < 1.5

        def unsure(cwd, ctx, budget):
            return {"pins": [], "ranked": [{"path": f"f{i}.py"} for i in range(10)], "k": 12}

        h.engine.context_provider = unsure
        assert h.prompt("New task: refactor the logging.") == {}         # no clear winner: say nothing


def test_hybrid_search_adds_ranker_picks():
    cat, _ = benchmark.reference()

    class FakeRanker:
        def rank(self, query, tools):
            return ["slack.add_reaction", "github.list_issues"]

    res = search.search(cat, "react with a thumbs up to that message", ranker=FakeRanker())
    ids = [r["tool_id"] for r in res["results"]]
    assert res["mode"] == "hybrid" and "slack.add_reaction" in ids and len(ids) == len(set(ids)) <= 8

    class Broken:
        def rank(self, query, tools):
            raise RuntimeError("encoder down")

    plain = search.search(cat, "read a local file", ranker=Broken())         # falls back to lexical, still answers
    assert plain["results"]
