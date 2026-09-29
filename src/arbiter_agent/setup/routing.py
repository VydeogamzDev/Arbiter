"""``arbiter routing``: cheap model first, Arbiter's checks keep it honest (opt-in, reversible).

Measured 2026-09-29 (bench/README.md, "Luna-first routing"): Codex on gpt-6-luna with Arbiter testing
every code-changing turn passed 24/24 planted-bug tasks, 14/15 sympy and 15/15 date-fns sessions at
$0.004-0.006 each, against gpt-6-sol at high effort: 21/24, 12/15, 15/15 at $0.13-0.35. The saving comes
from the model; Arbiter's part is making the cheap model safe (``routing.enabled`` in the daemon: every
turn that changed code is tested at stop, a failing turn goes back to the agent, and repeated failures
tell the user to switch the thread to the strong model).

``enable`` turns that on in Arbiter's config. With ``codex_default`` it also sets Codex's default model
and effort (top-level ``model`` / ``model_reasoning_effort`` in config.toml), after backing the file up;
the previous values are recorded, and ``disable`` restores them unless the user has changed them since.
Hooks can't switch a Codex turn's model, so the default is how the cheap model gets used there.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

import yaml

from arbiter_agent.clients.client_env import ClientEnv, current_env
from arbiter_agent.paths import ArbiterPaths, write_private

CHEAP = {"model": "gpt-6-luna", "model_reasoning_effort": "medium"}
_KEY = re.compile(r"^(model|model_reasoning_effort)\s*=\s*(.*?)\s*(#.*)?$")


def _record_path(paths: ArbiterPaths) -> Path:
    return paths.state / "routing.json"


def load_record(paths: ArbiterPaths) -> dict[str, Any] | None:
    try:
        return json.loads(_record_path(paths).read_text("utf-8"))
    except (OSError, ValueError):
        return None


def _set_arbiter(paths: ArbiterPaths, enabled: bool) -> None:
    cfg = paths.config_file
    data = (yaml.safe_load(cfg.read_text(encoding="utf-8")) if cfg.exists() else None) or {}
    data.setdefault("routing", {})["enabled"] = enabled
    cfg.parent.mkdir(parents=True, exist_ok=True)
    write_private(cfg, yaml.safe_dump(data, sort_keys=False).encode())


def top_level(text: str) -> dict[str, str]:
    """``model`` / ``model_reasoning_effort`` as written before the first table (raw TOML values)."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        if line.lstrip().startswith("["):
            break
        m = _KEY.match(line.strip())
        if m:
            out[m.group(1)] = m.group(2)
    return out


def set_top_level(text: str, values: dict[str, str | None]) -> str:
    """Set (or, with None, remove) top-level keys; other lines are kept as they are."""
    lines = text.splitlines()
    first_table = next((i for i, ln in enumerate(lines) if ln.lstrip().startswith("[")), len(lines))
    head, rest = lines[:first_table], lines[first_table:]
    todo = dict(values)
    new_head: list[str] = []
    for ln in head:
        m = _KEY.match(ln.strip())
        if m and m.group(1) in todo:
            v = todo.pop(m.group(1))
            if v is not None:
                new_head.append(f"{m.group(1)} = {v}")
            continue
        new_head.append(ln)
    added = [f"{k} = {v}" for k, v in todo.items() if v is not None]
    body = added + new_head
    if rest and body and body[-1].strip():
        body.append("")
    return "\n".join(body + rest) + ("\n" if (body or rest) else "")


def enable(paths: ArbiterPaths, *, codex_default: bool = False, dry_run: bool = False,
           env: ClientEnv | None = None) -> str:
    lines = ["Routing: Arbiter tests every turn that changed code and sends a failing one back to the agent; "
             "after repeated failures on a cheap model it recommends switching the thread to gpt-6-sol."]
    target = None
    before: dict[str, str] = {}
    if codex_default:
        env = env or current_env()
        target = env.codex_config
        text = target.read_text("utf-8") if target.exists() else ""
        before = top_level(text)
        lines.append(f"Codex default ({target}): model {before.get('model', '(unset)')} -> \"{CHEAP['model']}\", "
                     f"effort {before.get('model_reasoning_effort', '(unset)')} -> "
                     f"\"{CHEAP['model_reasoning_effort']}\"")
    if dry_run:
        return "\n".join(lines + ["(dry run: nothing written)"])
    _set_arbiter(paths, True)
    rec: dict[str, Any] = {"at": time.time(), "codex": None}
    if target is not None:
        backup = None
        if target.exists():
            paths.backups.mkdir(parents=True, exist_ok=True)
            backup = paths.backups / f"codex-config.routing.{time.strftime('%Y%m%d-%H%M%S')}.toml"
            shutil.copyfile(target, backup)
        text = target.read_text("utf-8") if target.exists() else ""
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(set_top_level(text, {k: json.dumps(v) for k, v in CHEAP.items()}), encoding="utf-8")
        rec["codex"] = {"config": str(target), "before": before, "set": {k: json.dumps(v) for k, v in CHEAP.items()},
                        "backup": str(backup) if backup else None}
    paths.state.mkdir(parents=True, exist_ok=True)
    write_private(_record_path(paths), json.dumps(rec, indent=2).encode())
    return "\n".join(lines + ["Done. Restart the daemon (`arbiter daemon restart`) and start a new Codex thread."])


def disable(paths: ArbiterPaths) -> str:
    rec = load_record(paths) or {}
    _set_arbiter(paths, False)
    out = ["routing: off"]
    codex = rec.get("codex")
    if codex:
        target = Path(codex["config"])
        if target.exists():
            text = target.read_text("utf-8")
            now = top_level(text)
            restore: dict[str, str | None] = {}
            for k, v in codex["set"].items():
                if now.get(k) == v:                      # only if the user hasn't changed it since
                    restore[k] = codex["before"].get(k)
            target.write_text(set_top_level(text, restore), encoding="utf-8")
            out.append(f"Codex default restored in {target}: {', '.join(restore) or 'nothing (changed since)'}")
    _record_path(paths).unlink(missing_ok=True)
    return "\n".join(out)


def status(paths: ArbiterPaths, env: ClientEnv | None = None) -> str:
    rec = load_record(paths)
    if not rec:
        return "routing: off. `arbiter routing enable` (add `--codex-default` to make gpt-6-luna Codex's default)"
    env = env or current_env()
    now = top_level(env.codex_config.read_text("utf-8")) if env.codex_config.exists() else {}
    return (f"routing: on; Codex default model {now.get('model', '(unset)')}, "
            f"effort {now.get('model_reasoning_effort', '(unset)')}")
