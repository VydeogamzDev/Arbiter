"""Sensor benchmark (M7.8): labeled judgments per decision family, scored for any backend next to
the rules baseline. Items come from the eval corpus (claims, epochs, contracts), so the rules and
the sensor are measured on the same data.

Metrics per family:
- coverage (answered / total) and balanced accuracy on answered items;
- Brier score and 10-bin ECE of the positive-option score (``model_score`` until calibrated);
- latency p50/p95;
- the rules baseline's balanced accuracy on all items.
"""

from __future__ import annotations

import statistics
import time
from pathlib import Path
from typing import Any

import yaml

from arbiter_agent.completion.claim_detection import classify
from arbiter_agent.semif.service import SemIfService
from arbiter_agent.semif.shadow import question
from arbiter_agent.semif.types import StateSection
from arbiter_agent.state import goal_epochs
from arbiter_agent.state.contract_coverage import requirement_reason
from arbiter_agent.state.text_norm import norm, sentences

CORPUS = Path(__file__).resolve().parent.parent / "eval" / "corpus"


def items(corpus: Path | None = None) -> list[dict[str, Any]]:
    corpus = corpus or CORPUS
    out: list[dict[str, Any]] = []
    claims = yaml.safe_load((corpus / "claims.yaml").read_text(encoding="utf-8"))
    for label, key in ((True, "claims"), (False, "non_claims")):
        for msg in claims[key]:
            out.append({"family": "completion_claim", "sections": [StateSection("final assistant message", msg, 0,
                                                                                   True)],
                        "label": label, "rule": classify(msg).gated})
    for seq in yaml.safe_load((corpus / "epochs.yaml").read_text(encoding="utf-8"))["sequences"]:
        prev, first, resume = None, True, False
        for p in seq["prompts"]:
            if p["text"] == "@resume":
                resume = True
                continue
            if not first:   # the first prompt of a session is always a new task: nothing to judge
                secs = [StateSection("latest user message", p["text"], 0, True)]
                if prev:
                    secs.insert(0, StateSection("previous user message", prev, 20))
                rule = goal_epochs.decide(p["text"], first_in_session=False, after_resume=resume).action
                out.append({"family": "scope_change", "sections": secs, "label": p["expect"] == "confirm_new",
                            "rule": rule == "confirm_new"})
            prev, first, resume = p["text"], False, False
    for case in yaml.safe_load((corpus / "contracts.yaml").read_text(encoding="utf-8"))["cases"]:
        reqs = [norm(r) for r in case["requirements"]]
        for text in case["prompts"]:
            for s in sentences(text):
                label = any(r in norm(s) for r in reqs)
                out.append({"family": "requirement_detection", "sections": [StateSection("sentence", s, 0, True)],
                            "label": label, "rule": requirement_reason(s) is not None})
    return out


def _balanced_accuracy(pairs: list[tuple[bool, bool]]) -> float | None:
    pos = [p for p in pairs if p[0]]
    neg = [p for p in pairs if not p[0]]
    if not pos or not neg:
        return None
    tpr = sum(1 for lab, pred in pos if pred) / len(pos)
    tnr = sum(1 for lab, pred in neg if not pred) / len(neg)
    return round((tpr + tnr) / 2, 3)


def _ece(scores: list[tuple[float, bool]], bins: int = 10) -> float | None:
    if not scores:
        return None
    total = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        bucket = [(p, y) for p, y in scores if (lo <= p < hi) or (b == bins - 1 and p == 1.0)]
        if bucket:
            conf = sum(p for p, _ in bucket) / len(bucket)
            acc = sum(1 for _, y in bucket if y) / len(bucket)
            total += len(bucket) / len(scores) * abs(conf - acc)
    return round(total, 4)


def run(service: SemIfService, corpus: Path | None = None, deadline_s: float = 30.0) -> dict[str, Any]:
    by_family: dict[str, list[dict[str, Any]]] = {}
    for it in items(corpus):
        by_family.setdefault(it["family"], []).append(it)
    report: dict[str, Any] = {"families": {}}
    for family, its in by_family.items():
        answered: list[tuple[bool, bool]] = []
        scored: list[tuple[float, bool]] = []
        lat: list[float] = []
        for it in its:
            q = question(family, it["sections"])
            t0 = time.perf_counter()
            j = service.judge(q, deadline_s)
            lat.append((time.perf_counter() - t0) * 1000)
            positive = q.binary_positive or q.options[0]
            if j.probs:
                scored.append((j.probs.get(positive, 0.0), it["label"]))
            if not j.abstain and j.choice is not None:
                answered.append((it["label"], j.choice == positive))
        brier = (round(statistics.fmean((p - (1.0 if y else 0.0)) ** 2 for p, y in scored), 4) if scored else None)
        report["families"][family] = {
            "items": len(its), "coverage": round(len(answered) / len(its), 3),
            "balanced_accuracy": _balanced_accuracy(answered), "brier": brier, "ece": _ece(scored),
            "latency_p50_ms": round(statistics.median(lat), 2) if lat else None,
            "latency_p95_ms": round(sorted(lat)[max(0, round(0.95 * (len(lat) - 1)))], 2) if lat else None,
            "rules_balanced_accuracy": _balanced_accuracy([(it["label"], it["rule"]) for it in its]),
        }
    report["backends"] = {"decoder": service.decoder.name, "encoder": service.encoder.name,
                          "encoder_families": sorted(service.encoder_families)}
    return report


def render(report: dict[str, Any]) -> str:
    b = report["backends"]
    lines = [f"Sensor benchmark (decoder: {b['decoder']}, encoder: {b['encoder']} for {b['encoder_families'] or '-'})",
             "", f"{'family':<18}{'items':>6}{'coverage':>10}{'bal.acc':>9}{'brier':>8}{'ece':>8}{'p95 ms':>9}"
                 f"{'rules':>8}"]
    for fam, m in report["families"].items():
        def f(v: Any) -> str:
            return "-" if v is None else str(v)
        lines.append(f"{fam:<18}{m['items']:>6}{f(m['coverage']):>10}{f(m['balanced_accuracy']):>9}"
                     f"{f(m['brier']):>8}{f(m['ece']):>8}{f(m['latency_p95_ms']):>9}"
                     f"{f(m['rules_balanced_accuracy']):>8}")
    return "\n".join(lines)
