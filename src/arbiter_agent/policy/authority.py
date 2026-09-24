"""Who may change which control (spec §15.5, §24; decisions 0004, 0005).

Actors, from the daemon's point of view:

- ``user_tty``: the CLI after an interactive confirmation (like ``arbiter verify --trust``);
- ``user_cli``: the CLI without one (scripts, or an agent running the CLI in its own shell);
- ``agent``: a request from the MCP shim, i.e. the coding agent itself;
- ``controller``: Arbiter's own components (breakers, shedding).

Agents may ask for optimization bypasses for their next turn, and nothing else: they can never
weaken the gate, disable integrity modules, reset integrity breakers or turn the controller off.
Controls that weaken integrity need an interactive user.
"""

from __future__ import annotations

from enum import IntEnum


class Actor(IntEnum):
    AGENT = 1
    USER_CLI = 2
    USER_TTY = 3
    CONTROLLER = 4          # internal; not reachable over IPC


def actor_for(component: str | None, channel: str | None) -> Actor:
    if component == "cli":
        return Actor.USER_TTY if channel == "cli_tty" else Actor.USER_CLI
    return Actor.AGENT


# Modules whose disabling weakens integrity (completion evidence, gate, audit, privacy).
INTEGRITY_MODULES = frozenset({"event_log", "redaction", "task_state", "contracts", "test_integrity",
                               "completion_gate", "circuit_breakers", "controller"})

AGENT_CONTROLS = frozenset({"next_turn:bypass_retrieval_narrowing", "next_turn:normal_tool_surface",
                            "next_turn:full_review"})


def required(control: str, value: object = None) -> Actor:
    """The least-privileged actor allowed to set ``control`` to ``value``."""
    if control in AGENT_CONTROLS:
        return Actor.AGENT
    if control.startswith("module:"):
        flag = control.split(":", 1)[1]
        turning_off = value in (False, "off", 0)
        return Actor.USER_TTY if (flag in INTEGRITY_MODULES and turning_off) else Actor.USER_CLI
    if control == "controller":
        return Actor.USER_TTY if value in (False, "off", 0) else Actor.USER_CLI
    if control.startswith("breaker_reset:"):
        return Actor.USER_TTY if control.endswith(":integrity") else Actor.USER_CLI
    return Actor.USER_CLI


def check(actor: Actor, control: str, value: object = None) -> str | None:
    """None when allowed, else the reason it isn't."""
    need = required(control, value)
    if actor >= need:
        return None
    if need == Actor.USER_TTY:
        return (f"{control} weakens integrity: run it yourself in an interactive terminal "
                "(`arbiter control ...` asks for confirmation)")
    return f"{control} can only be changed by the user with `arbiter control ...`"
