"""Where does a condition gain or lose efficiency? Decompose each run from its Claude transcript.

    python -m bench.analyze D:/ArbiterBench/runs/<run_id> [--vs baseline --cond full]

Per run it reads every API call (deduplicated by message id) and splits cost into:
- cache writes (new context: the prompt, the pack, tool results, the model's own output fed back);
- cache reads (the whole context re-read on every call);
- output, separated into thinking and visible tokens.
Prices are fitted per model from the runs' own total_cost_usd (least squares), so nothing is
assumed. It also counts tool calls by kind (orientation, read, edit, test, other shell, Arbiter,
other) and times the prompt hook, then prints paired per-task differences.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ORIENT = ("glob", "grep", "ls", "toolsearch")
ORIENT_SHELL = ("get-childitem", "ls ", "dir ", "git ls-files", "tree", "find ", "select-string", "git status")


def _ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def tool_kind(name: str, inp: dict[str, Any]) -> str:
    n = name.lower()
    if n.startswith("mcp__arbiter"):
        return "arbiter"
    if n in ORIENT:
        return "orient"
    if n == "read":
        return "read"
    if n in ("edit", "write", "multiedit", "notebookedit"):
        return "edit"
    if n in ("powershell", "bash"):
        cmd = str(inp.get("command") or "").lower()
        if "pytest" in cmd or "unittest" in cmd:
            return "test"
        if any(k in cmd for k in ORIENT_SHELL) or cmd.startswith(("cat ", "get-content", "type ")):
            return "orient"
        return "shell"
    return "other"


def parse_run(result: Path) -> dict[str, Any] | None:
    r = json.loads(result.read_text("utf-8"))
    trs = sorted((result.parent / "transcript").glob("*.jsonl"))
    if not trs:
        return None
    calls: dict[str, dict[str, Any]] = {}
    tools: Counter[str] = Counter()
    first_user = pack_at = None
    pack_tokens = 0
    for tr in trs:
        for line in tr.read_text("utf-8", errors="replace").splitlines():
            try:
                o = json.loads(line)
            except ValueError:
                continue
            ts = o.get("timestamp")
            if o.get("type") == "user" and first_user is None and ts and not o.get("toolUseResult"):
                first_user = _ts(ts)
            att = o.get("attachment") or {}
            if att.get("type") == "hook_additional_context" and pack_at is None and ts:
                pack_at = _ts(ts)
                pack_tokens = sum(len(c) for c in att.get("content") or []) // 4
            if o.get("type") == "assistant":
                m = o.get("message") or {}
                if m.get("id") and m.get("usage"):
                    calls[m["id"]] = m["usage"]
                for c in m.get("content") or []:
                    if isinstance(c, dict) and c.get("type") == "tool_use":
                        tools[tool_kind(c.get("name", ""), c.get("input") or {})] += 1
    u = list(calls.values())
    # Token totals come from the CLI's own per-invocation usage (transcript entries are streamed and
    # can carry partial usage); the transcript still gives call counts, tools and timing.
    tu = [t.get("usage") or {} for t in r.get("turns") or []]
    thinking = sum(((x.get("output_tokens_details") or {}).get("thinking_tokens") or 0) for x in tu)
    out = sum(x.get("output_tokens", 0) for x in tu)
    return {
        "task": r["task"], "condition": r["condition"], "rep": r["rep"], "model": r.get("model"),
        "cost": r.get("cost_usd") or 0.0, "calls": len(u),
        "input": sum(x.get("input_tokens", 0) for x in tu),
        "cache_write": sum(x.get("cache_creation_input_tokens", 0) for x in tu),
        "cache_read": sum(x.get("cache_read_input_tokens", 0) for x in tu),
        "output": out, "thinking": thinking, "visible": out - thinking,
        "first_call_write": (u[0].get("cache_creation_input_tokens", 0) if u else 0),
        "tools": dict(tools), "pack_tokens": pack_tokens,
        "prompt_hook_s": round(pack_at - first_user, 2) if pack_at and first_user else None,
        "wall_s": r.get("agent_wall_s") or 0.0,
        "pass": (r.get("score") or {}).get("full_pass"),
    }


RATIOS = {"input": 1.0, "cache_write": 2.0, "cache_read": 0.1, "output": 5.0}   # 1h cache writes = 2x input


def fit_prices(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Per-token prices from the runs' reported costs: one base price per model, with the fixed
    ratios above (a free four-way fit is ill-conditioned because uncached input is ~0)."""
    units = [sum(r[k] * v for k, v in RATIOS.items()) for r in rows]
    base = sum(u * r["cost"] for u, r in zip(units, rows, strict=True)) / max(1e-9, sum(u * u for u in units))
    err = [abs(u * base - r["cost"]) / r["cost"] for u, r in zip(units, rows, strict=True) if r["cost"]]
    out = {k: base * v for k, v in RATIOS.items()}
    out["fit_err"] = sum(err) / len(err) if err else 0.0
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m bench.analyze")
    p.add_argument("run_dirs", nargs="+", type=Path)
    p.add_argument("--vs", default="baseline")
    p.add_argument("--cond", default="full")
    a = p.parse_args(argv)
    rows = [x for d in a.run_dirs for f in sorted(d.glob("*/*/rep*/result.json")) if (x := parse_run(f))]
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_model[str(r["model"])].append(r)
    for model, rs in by_model.items():
        pr = fit_prices(rs)
        prices = ", ".join(f"{k} {v * 1e6:.2f}" for k, v in pr.items() if k != "fit_err")
        print(f"\n=== {model}: {len(rs)} runs; fitted $/Mtok: {prices}; mean fit error {pr['fit_err'] * 100:.1f}%")
        for r in rs:
            r["$write"] = r["cache_write"] * pr["cache_write"]
            r["$read"] = r["cache_read"] * pr["cache_read"]
            r["$think"] = r["thinking"] * pr["output"]
            r["$visible"] = r["visible"] * pr["output"]
        pairs = defaultdict(dict)
        for r in rs:
            pairs[(r["task"], r["rep"])][r["condition"]] = r
        keys = ["cost", "calls", "$write", "$read", "$think", "$visible", "cache_write", "thinking", "visible",
                "wall_s"]
        tot = {k: [0.0, 0.0] for k in keys}
        kinds = sorted({k for r in rs for k in r["tools"]})
        tk = {k: [0, 0] for k in kinds}
        print(f"{'task':22s} {'cost b->c':>15s} {'calls':>7s} {'$write':>13s} {'$read':>13s} {'$think':>13s} "
              f"{'$visible':>13s} pack  hook_s  tools b | c")
        for (task, rep), d in sorted(pairs.items()):
            if a.vs not in d or a.cond not in d:
                continue
            b, c = d[a.vs], d[a.cond]
            for k in keys:
                tot[k][0] += b[k]
                tot[k][1] += c[k]
            for k in kinds:
                tk[k][0] += b["tools"].get(k, 0)
                tk[k][1] += c["tools"].get(k, 0)
            def fmt(k: str, b: dict[str, Any] = b, c: dict[str, Any] = c) -> str:
                return f"{b[k]:.3f}>{c[k]:.3f}"
            tb = " ".join(f"{k[:3]}{b['tools'].get(k, 0)}" for k in kinds)
            tc = " ".join(f"{k[:3]}{c['tools'].get(k, 0)}" for k in kinds)
            print(f"{task[:20]:20s}{rep} {fmt('cost'):>15s} {b['calls']:>3}>{c['calls']:<3} {fmt('$write'):>13s} "
                  f"{fmt('$read'):>13s} {fmt('$think'):>13s} {fmt('$visible'):>13s} {c['pack_tokens']:>5} "
                  f"{c['prompt_hook_s'] or 0:>5.1f}  {tb} | {tc}")
        print("\nTOTAL (baseline -> condition, change):")
        for k in keys:
            bb, cc = tot[k]
            print(f"  {k:12s} {bb:12.3f} -> {cc:12.3f}   {cc - bb:+12.3f} ({(cc / bb - 1) * 100 if bb else 0:+.0f}%)")
        print("  tool calls by kind: " + ", ".join(f"{k} {tk[k][0]}->{tk[k][1]}" for k in kinds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
