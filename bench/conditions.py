"""Benchmark conditions: which Arbiter pieces a run gets.

Every condition runs the same `claude -p` command with the user's own settings files excluded
(`--setting-sources project`) and only the MCP servers listed here (`--strict-mcp-config`), so the
real Arbiter install never sees benchmark runs. `hooks` are Claude Code hook events wired to a
fresh per-run daemon; `mcp` adds the Arbiter MCP server; `config` is that run's Arbiter config.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ALL_HOOKS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop",
             "SubagentStop", "PreCompact", "SessionEnd")


@dataclass(frozen=True)
class Condition:
    name: str
    summary: str
    hooks: tuple[str, ...] = ()
    mcp: bool = False
    config: dict[str, Any] = field(default_factory=dict)
    encoder: bool = False           # load the tier-0 ONNX encoder (sensor shadow + semantic tool search)
    slim: str | None = None         # arbiter slim profile applied to the Claude settings (standard | lean)
    agent_env: tuple[tuple[str, str], ...] = ()   # extra environment for the agent process
    codex_config: tuple[str, ...] = ()            # extra `codex exec -c key=value` settings
    workspace_files: tuple[tuple[str, str], ...] = ()   # (path, text) written into the workspace, git-excluded
    effort_router: bool = False                   # codex: system 1 picks low/high effort per prompt (user applies it)
    handoff: bool = False                         # codex: a prompt system 1 calls a new task starts a fresh thread

    @property
    def uses_arbiter(self) -> bool:
        return bool(self.hooks or self.mcp)


DELEGATE_AGENTS_MD = """# Working in this repository

Delegate code changes: for each request that changes code, spawn one sub-agent and give it the whole
task (what to change, the tests to add or update, how to check the result). Don't explore or edit the
code yourself first. When the sub-agent finishes, review its diff and the test result, fix anything
that is wrong, and reply to the user.
"""
BLOCK = {"completion": {"gate_mode": "block"}}
# System 1 through the benchmark's own Codex sign-in (gpt-6-luna), never the user's.
S1 = {"backend": "codex", "codex_bin": "D:/ArbiterBench/bin/codex.exe", "codex_home": "D:/ArbiterBench/codex-home",
      "debug_log": True}
TRUST = {"completion": {"auto_test_trust_note": True, "auto_test_client_budget_s": {"codex": 4.3, "pi": 8.0}}}
CONTEXT = {"retrieval": {"auto_context": True}, "ui": {"inject_status": True}}


def _merge(*parts: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for p in parts:
        for k, v in p.items():
            out[k] = {**out.get(k, {}), **v} if isinstance(v, dict) else v
    return out


CONDITIONS: dict[str, Condition] = {c.name: c for c in [
    Condition("baseline", "plain Claude Code: no Arbiter at all"),
    Condition("full", "everything: hooks, MCP tools, completion gate in block mode, auto file context, "
                      "status summaries, encoder sensor in shadow",
              hooks=ALL_HOOKS, mcp=True, config=_merge(BLOCK, CONTEXT), encoder=True),
    Condition("full_slim", "full, plus `arbiter slim` lean: Claude Code features coding doesn't use are off",
              hooks=ALL_HOOKS, mcp=True, config=_merge(BLOCK, CONTEXT), encoder=True, slim="lean"),
    Condition("full_map", "full, but the context pack is map-only (repo map + ranked likely files, no contents)",
              hooks=ALL_HOOKS, mcp=True, encoder=True,
              config=_merge(BLOCK, CONTEXT, {"retrieval": {"auto_context_pack_contents": False}})),
    Condition("full_tools", "full, plus Arbiter's Pi edit tools (insert_code, replace_def: no old code repeated)",
              hooks=ALL_HOOKS, mcp=True, config=_merge(BLOCK, CONTEXT), encoder=True,
              agent_env=(("ARBITER_PI_EDIT_TOOLS", "1"),)),
    Condition("full_delegate", "full, plus Codex sub-agents on gpt-6-luna and an instruction to delegate a "
                               "well-scoped change to one, then review its diff (main agent keeps its model)",
              hooks=ALL_HOOKS, mcp=True, config=_merge(BLOCK, CONTEXT), encoder=True,
              codex_config=('agents.default_subagent_model="gpt-6-luna"',
                            'agents.default_subagent_reasoning_effort="medium"'),
              workspace_files=(("AGENTS.md", DELEGATE_AGENTS_MD),)),
    Condition("full_patch","full, plus Arbiter's Pi apply_patch tool (Codex's patch format: several files in one "
                            "call)",
              hooks=ALL_HOOKS, mcp=True, config=_merge(BLOCK, CONTEXT), encoder=True,
              agent_env=(("ARBITER_PI_APPLY_PATCH", "1"),)),
    Condition("full_trust", "full, plus: a post-edit test run still in progress is announced, a result says it's "
                            "current, and Codex's post-edit window is 4.3 s (sol re-ran tests before results arrived)",
              hooks=ALL_HOOKS, mcp=True, encoder=True, config=_merge(BLOCK, CONTEXT, TRUST)),
    Condition("full_s1", "full_trust, plus system 1 (gpt-6-luna): a task brief, a review of the diff at stop, and "
                         "low/high effort per prompt",
              hooks=ALL_HOOKS, mcp=True, encoder=True, effort_router=True,
              config=_merge(BLOCK, CONTEXT, TRUST, {"system1": {**S1, "brief": True, "judge": True}})),
    Condition("full_compact", "full, plus Codex's auto-compaction at 60k tokens of context (long sessions)",
              hooks=ALL_HOOKS, mcp=True, encoder=True, config=_merge(BLOCK, CONTEXT),
              codex_config=("model_auto_compact_token_limit=60000",)),
    Condition("full_handoff", "full, plus a fresh thread when system 1 calls a prompt a new, unrelated task (the user "
                              "following Arbiter's advice)",
              hooks=ALL_HOOKS, mcp=True, encoder=True, handoff=True, config=_merge(BLOCK, CONTEXT)),
    Condition("full_brief", "full, plus a system-1 task brief", hooks=ALL_HOOKS, mcp=True, encoder=True,
              config=_merge(BLOCK, CONTEXT, {"system1": {**S1, "brief": True}})),
    Condition("full_judge", "full, plus a system-1 review of the diff at stop", hooks=ALL_HOOKS, mcp=True, encoder=True,
              config=_merge(BLOCK, CONTEXT, {"system1": {**S1, "judge": True}})),
    Condition("full_route", "full, plus routing: every code-changing turn tested at stop, failures sent back, "
                            "repeated failures on a cheap model escalate (with --escalate-model the harness switches "
                            "the thread, as the user would)",
              hooks=ALL_HOOKS, mcp=True, encoder=True, config=_merge(BLOCK, CONTEXT, {"routing": {"enabled": True}})),
    Condition("full_cxslim","full, plus Codex features coding doesn't use switched off (apps, plugins, tool "
                             "suggestions, browser, computer use, image generation, sleep, goals): -17% fixed prefix",
              hooks=ALL_HOOKS, mcp=True, config=_merge(BLOCK, CONTEXT), encoder=True,
              codex_config=tuple(f"features.{f}=false" for f in (
                  "apps", "plugins", "tool_suggest", "browser_use", "computer_use", "image_generation", "sleep_tool",
                  "goals", "skill_search"))),
    # Ablations: one subsystem at a time.
    # The gate checks contracts the agent records through the MCP tools, so it needs both halves.
    Condition("gate_only", "completion gate in block mode: hooks + MCP contract tools, no injected context",
              hooks=ALL_HOOKS, mcp=True, config=BLOCK),
    Condition("context_only", "auto file context + status summaries (hooks, annotate gate, no MCP tools)",
              hooks=ALL_HOOKS, config=CONTEXT),
    Condition("tools_only", "Arbiter MCP tools only (search, contracts, verify, finish check); no hooks, "
                            "so nothing is enforced",
              mcp=True),
    # Hook-only ablations (Pi has no MCP): the context pack alone, and Arbiter's own test runs plus
    # the completion gate alone.
    Condition("pack_only", "context pack + status summaries; no Arbiter test runs, gate annotates only",
              hooks=ALL_HOOKS, encoder=True, config=_merge(CONTEXT, {"completion": {"auto_test": "off"}})),
    Condition("tests_gate_only", "Arbiter's post-edit test runs + completion gate in block mode; no context pack",
              hooks=ALL_HOOKS, mcp=True, encoder=True,
              config=_merge(BLOCK, {"retrieval": {"auto_context": False}, "ui": {"inject_status": True}})),
    Condition("observe_only","hooks record everything but change nothing (annotate, nothing injected): "
                              "pure overhead",
              hooks=ALL_HOOKS),
]}

PRIMARY = ("baseline", "full")
ABLATIONS = ("gate_only", "context_only", "tools_only", "observe_only")
