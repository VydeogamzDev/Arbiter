"""``arbiter routing``: cheap model first, Arbiter's checks keep it honest (opt-in, reversible).

Measured 2026-09-29 (bench/README.md, "Luna-first routing"): Codex on gpt-6-luna with Arbiter testing
every code-changing turn passed 24/24 planted-bug tasks, 14/15 sympy and 15/15 date-fns sessions at
$0.004-0.006 each, against gpt-6-sol at high effort: 21/24, 12/15, 15/15 at $0.13-0.35. The saving comes
from the model; Arbiter's part is making the cheap model safe (``routing.enabled`` in the daemon: every
turn that changed code is tested at stop, a failing turn goes back to the agent, and repeated failures
tell the user to switch the thread to the strong model).

Same model, measured 2026-09-29 on the user's real setup (gpt-6-sol): running routine prompts at low
effort cut Codex + Arbiter's cost 42-51% at the same pass rate (``--codex-effort low``; repeated failures
then recommend high effort). Set the effort for a thread and keep it: changing it mid-thread served the next
request's history uncached (64% cached vs 95% without a change), as Codex drops the earlier turns' reasoning.

``enable`` turns routing on in Arbiter's config. The Codex options set top-level keys in config.toml
(``model``, ``model_reasoning_effort``) after backing the file up; the
previous values are recorded, and ``disable`` restores each one unless the user has changed it since.
Hooks can't switch a Codex turn's model or effort, so the defaults are how these get used there.
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
KEYS = ("model", "model_reasoning_effort")
_KEY = re.compile(r"^(model|model_reasoning_effort)\s*=\s*(.*?)\s*(#.*)?$")


def _record_path(paths: ArbiterPaths) -> Path:
    return paths.state / "routing.json"


def load_record(paths: ArbiterPaths) -> dict[str, Any] | None:
    try:
        return json.loads(_record_path(paths).read_text("utf-8"))
    except (OSError, ValueError):
        return None


def _set_arbiter(paths: ArbiterPaths, enabled: bool, escalate: str | None = None) -> None:
    cfg = paths.config_file
    data = (yaml.safe_load(cfg.read_text(encoding="utf-8")) if cfg.exists() else None) or {}
    data.setdefault("routing", {})["enabled"] = enabled
    if escalate:
        data["routing"]["escalate"] = escalate
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


def enable(paths: ArbiterPaths, *, codex_default: bool = False, codex_effort: str | None = None,
           dry_run: bool = False, env: ClientEnv | None = None) -> str:
    values: dict[str, str] = {}
    if codex_default:
        values.update({k: json.dumps(v) for k, v in CHEAP.items()})
    if codex_effort:
        values["model_reasoning_effort"] = json.dumps(codex_effort)
    escalate = "effort" if codex_effort and not codex_default else "model"
    lines = ["Routing: Arbiter tests every turn that changed code and sends a failing one back to the agent; after "
             "repeated failures it recommends " + ("high reasoning effort for the thread." if escalate == "effort"
                                                   else "switching the thread to gpt-6-sol.")]
    target = None
    before: dict[str, str] = {}
    if values:
        env = env or current_env()
        target = env.codex_config
        text = target.read_text("utf-8") if target.exists() else ""
        before = top_level(text)
        for k, v in values.items():
            lines.append(f"Codex ({target}): {k} {before.get(k, '(unset)')} -> {v}")
    if dry_run:
        return "\n".join(lines + ["(dry run: nothing written)"])
    _set_arbiter(paths, True, escalate)
    rec: dict[str, Any] = {"at": time.time(), "codex": None}
    if target is not None:
        backup = None
        if target.exists():
            paths.backups.mkdir(parents=True, exist_ok=True)
            backup = paths.backups / f"codex-config.routing.{time.strftime('%Y%m%d-%H%M%S')}.toml"
            shutil.copyfile(target, backup)
        text = target.read_text("utf-8") if target.exists() else ""
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(set_top_level(text, dict(values)), encoding="utf-8")
        rec["codex"] = {"config": str(target), "before": before, "set": values,
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
        return ("routing: off. `arbiter routing enable --codex-effort low` (sol at low effort, high when it "
                "struggles) or `--codex-default` (gpt-6-luna)")
    env = env or current_env()
    now = top_level(env.codex_config.read_text("utf-8")) if env.codex_config.exists() else {}
    return (f"routing: on; Codex model {now.get('model', '(unset)')}, effort "
            f"{now.get('model_reasoning_effort', '(unset)')}")
