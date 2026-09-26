"""Aggregate a benchmark run: per-condition rates, paired differences against baseline with
bootstrap confidence intervals (resampling tasks), and a per-category breakdown."""

from __future__ import annotations

import json
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from bench.conditions import CONDITIONS

METRICS = [  # key, label, higher_is_better, kind
    ("full_pass", "task success (all hidden tests)", True, "rate"),
    ("coverage", "requirement coverage", True, "mean"),
    ("false_complete", "false 'done' claims", False, "rate"),
    ("tampered", "test tampering", False, "rate"),
    ("cost_usd", "cost per run (USD)", False, "mean"),
    ("num_turns", "agent turns", False, "mean"),
    ("output_tokens", "output tokens", False, "mean"),
    ("agent_wall_s", "agent wall time (s)", False, "mean"),
]


def load(run_dir: Path) -> list[dict[str, Any]]:
    return [json.loads(p.read_text("utf-8")) for p in sorted(run_dir.glob("*/*/rep*/result.json"))]


def value(r: dict[str, Any], key: str) -> float | None:
    s = r.get("score") or {}
    v = s.get(key) if key in s else r.get(key)
    if v is None:
        return None
    return float(v)


def bootstrap_diff(pairs: dict[str, list[tuple[float, float]]], n: int = 4000,
                   seed: int = 7) -> tuple[float, float, float]:
    """Mean paired difference (cond - base) and a 95% CI, resampling tasks (clusters of reps)."""
    tasks = list(pairs)
    def mean_diff(ts: list[str]) -> float:
        diffs = [c - b for t in ts for b, c in pairs[t]]
        return sum(diffs) / len(diffs) if diffs else 0.0
    point = mean_diff(tasks)
    rng = random.Random(seed)
    sims = sorted(mean_diff([rng.choice(tasks) for _ in tasks]) for _ in range(n))
    return point, sims[int(0.025 * n)], sims[int(0.975 * n) - 1]


def fmt(v: float | None, kind: str) -> str:
    if v is None:
        return "-"
    return f"{v * 100:.0f}%" if kind == "rate" else f"{v:.3g}"


def write(run_dir: Path) -> str:
    rows = load(run_dir)
    meta = json.loads((run_dir / "run.json").read_text("utf-8")) if (run_dir / "run.json").is_file() else {}
    conds = [c for c in meta.get("conditions", []) if any(r["condition"] == c for r in rows)] or \
        sorted({r["condition"] for r in rows})
    by: dict[str, dict[tuple[str, int], dict[str, Any]]] = defaultdict(dict)
    for r in rows:
        by[r["condition"]][(r["task"], r["rep"])] = r

    lines = [f"# Arbiter benchmark: {meta.get('run_id', run_dir.name)}", "",
             f"agent `{meta.get('agent')}`"
             + (f", model `{meta.get('model')}`" if meta.get("agent") == "claude" else "")
             + f", {len(meta.get('tasks', []))} tasks, {meta.get('reps')} rep(s), {len(rows)} runs", ""]
    errors = [r for r in rows if r.get("harness_error") or r.get("score_error")]
    if errors:
        lines += [f"**{len(errors)} run(s) had harness/scoring errors** (excluded where a metric is missing):", ""]
        lines += [f"- {r['condition']}/{r['task']}/rep{r['rep']}: "
                  f"{(r.get('harness_error') or r.get('score_error') or '').strip().splitlines()[-1]}" for r in errors]
        lines.append("")

    # Absolute table.
    lines += ["## Per condition", "", "| metric | " + " | ".join(conds) + " |", "|---|" + "---|" * len(conds)]
    summary: dict[str, dict[str, float | None]] = {}
    for key, label, _, kind in METRICS:
        cells = []
        for c in conds:
            vals = [v for v in (value(r, key) for r in by[c].values()) if v is not None]
            m = statistics.fmean(vals) if vals else None
            summary.setdefault(c, {})[key] = m
            cells.append(fmt(m, "rate" if kind == "rate" or key == "coverage" else "mean"))
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines.append("")

    # Paired differences vs baseline.
    if "baseline" in conds and len(conds) > 1:
        lines += ["## Against baseline (paired by task and rep; 95% bootstrap CI over tasks)", "",
                  "| condition | metric | baseline | condition | change | relative | 95% CI of change |",
                  "|---|---|---|---|---|---|---|"]
        for c in conds:
            if c == "baseline":
                continue
            for key, label, hib, kind in METRICS:
                pairs: dict[str, list[tuple[float, float]]] = defaultdict(list)
                for k, rb in by["baseline"].items():
                    rc = by[c].get(k)
                    if rc is None:
                        continue
                    vb, vc = value(rb, key), value(rc, key)
                    if vb is not None and vc is not None:
                        pairs[k[0]].append((vb, vc))
                if not pairs:
                    continue
                d, lo, hi = bootstrap_diff(pairs)
                base = statistics.fmean(b for ps in pairs.values() for b, _ in ps)
                cur = statistics.fmean(v for ps in pairs.values() for _, v in ps)
                pct = "rate" if kind == "rate" or key == "coverage" else "mean"
                change = f"{d * 100:+.0f} pts" if pct == "rate" else f"{d:+.3g}"
                rel = f"{d / base * 100:+.0f}%" if base else "-"
                ci = (f"[{lo * 100:+.0f}, {hi * 100:+.0f}] pts" if pct == "rate" else f"[{lo:+.3g}, {hi:+.3g}]")
                good = (d > 0) == hib if d else None
                mark = "" if good is None or (lo <= 0 <= hi) else (" (better)" if good else " (worse)")
                lines.append(f"| {c} | {label} | {fmt(base, pct)} | {fmt(cur, pct)} | {change}{mark} | {rel} | {ci} |")
        lines += ["", "A change is marked better/worse only when its 95% CI excludes zero. With few tasks the "
                  "intervals are wide; treat single-rep results as a pilot.", ""]

    # Per category.
    cats = sorted({r["category"] for r in rows})
    lines += ["## Task success by category", "", "| category | " + " | ".join(conds) + " |",
              "|---|" + "---|" * len(conds)]
    for cat in cats:
        cells = []
        for c in conds:
            rs = [r for r in by[c].values() if r["category"] == cat and r.get("score")]
            cells.append(f"{sum(r['score']['full_pass'] for r in rs)}/{len(rs)}" if rs else "-")
        lines.append(f"| {cat} | " + " | ".join(cells) + " |")
    lines.append("")

    # Per task detail.
    lines += ["## Per task (coverage; T = tampered, F = false done claim, B = stop blocks)", "",
              "| task | " + " | ".join(conds) + " |", "|---|" + "---|" * len(conds)]
    for t in sorted({r["task"] for r in rows}):
        cells = []
        for c in conds:
            rs = [r for (tt, _), r in sorted(by[c].items()) if tt == t]
            parts = []
            for r in rs:
                s = r.get("score") or {}
                flags = ("T" if s.get("tampered") else "") + ("F" if s.get("false_complete") else "")
                blocks = (r.get("arbiter") or {}).get("stop_blocks")
                parts.append(f"{s.get('coverage', 0) * 100:.0f}%{flags}" + (f" B{blocks}" if blocks else ""))
            cells.append(", ".join(parts) or "-")
        lines.append(f"| {t} | " + " | ".join(cells) + " |")
    lines.append("")

    # Arbiter activity.
    arb_conds = [c for c in conds if c in CONDITIONS and CONDITIONS[c].uses_arbiter]
    if arb_conds:
        lines += ["## Arbiter activity", "", "| condition | runs w/ events | stop blocks | ledger verdicts | "
                  "sensor rows | permission denials |", "|---|---|---|---|---|---|"]
        for c in arb_conds:
            rs = list(by[c].values())
            arbs = [r.get("arbiter") or {} for r in rs]
            verdicts: dict[str, int] = defaultdict(int)
            for a in arbs:
                for led in a.get("ledger", []):
                    verdicts[led["verdict"]] += 1
            lines.append(f"| {c} | {sum(1 for a in arbs if a.get('events'))}/{len(rs)} | "
                         f"{sum(a.get('stop_blocks', 0) for a in arbs)} | "
                         f"{', '.join(f'{k} {v}' for k, v in sorted(verdicts.items())) or '-'} | "
                         f"{sum(a.get('sensor_rows', 0) for a in arbs)} | "
                         f"{sum(r.get('permission_denials') or 0 for r in rs)} |")
        lines.append("")

    text = "\n".join(lines)
    (run_dir / "report.md").write_text(text, encoding="utf-8")
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return text
