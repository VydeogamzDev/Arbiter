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

    @property
    def uses_arbiter(self) -> bool:
        return bool(self.hooks or self.mcp)


BLOCK = {"completion": {"gate_mode": "block"}}
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
    Condition("full_tests", "full, plus each top pick's own test file (by repo layout) in the pack",
              hooks=ALL_HOOKS, mcp=True, encoder=True,
              config=_merge(BLOCK, CONTEXT, {"retrieval": {"auto_context_own_tests": True}})),
    Condition("full_big", "full_tests, plus a 6,000-token pack: no outlines, 12,000 characters per file",
              hooks=ALL_HOOKS, mcp=True, encoder=True,
              config=_merge(BLOCK, CONTEXT, {"retrieval": {"auto_context_own_tests": True,
                                                           "auto_context_pack_no_outlines": True,
                                                           "auto_context_pack_tokens": 6000,
                                                           "auto_context_pack_file_chars": 12000}})),
    Condition("full_patch","full, plus Arbiter's Pi apply_patch tool (Codex's patch format: several files in one "
                            "call)",
              hooks=ALL_HOOKS, mcp=True, config=_merge(BLOCK, CONTEXT), encoder=True,
              agent_env=(("ARBITER_PI_APPLY_PATCH", "1"),)),
    Condition("full_cxslim", "full, plus Codex features coding doesn't use switched off (apps, plugins, tool "
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
