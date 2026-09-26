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
    # Ablations: one subsystem at a time.
    # The gate checks contracts the agent records through the MCP tools, so it needs both halves.
    Condition("gate_only", "completion gate in block mode: hooks + MCP contract tools, no injected context",
              hooks=ALL_HOOKS, mcp=True, config=BLOCK),
    Condition("context_only", "auto file context + status summaries (hooks, annotate gate, no MCP tools)",
              hooks=ALL_HOOKS, config=CONTEXT),
    Condition("tools_only", "Arbiter MCP tools only (search, contracts, verify, finish check); no hooks, "
                            "so nothing is enforced",
              mcp=True),
    Condition("observe_only", "hooks record everything but change nothing (annotate, nothing injected): "
                              "pure overhead",
              hooks=ALL_HOOKS),
]}

PRIMARY = ("baseline", "full")
ABLATIONS = ("gate_only", "context_only", "tools_only", "observe_only")
