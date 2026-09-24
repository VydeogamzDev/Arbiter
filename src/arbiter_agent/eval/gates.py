"""Evaluation gates (spec §20.17) over the packaged corpus (M3.8, M4 exit metrics).

Every number here is computed by running the real code paths: runner parsers, claim detection,
epoch rules, the contract compiler and coverage check, the integrity checker, the loop detector,
and full traces through ingest + session engine + gate. The corpus is synthetic and hand-labeled
(see ``corpus/``); it is a regression floor, not a substitute for the real-trace cohorts in §20.
"""

from __future__ import annotations

import statistics
import time
from pathlib import Path
from typing import Any

import yaml

from arbiter_agent.completion.claim_detection import classify
from arbiter_agent.completion.gate import check_wording
from arbiter_agent.eval.trace_replay import Harness
from arbiter_agent.reasoning.loop_detector import LoopDetector
from arbiter_agent.state import contract_compiler, contract_coverage, goal_epochs
from arbiter_agent.state import intent as intent_log
from arbiter_agent.state.store import connect
from arbiter_agent.state.text_norm import norm
from arbiter_agent.telemetry import errors as err
from arbiter_agent.telemetry.runner_parsers import detect, fingerprint, junit_xml

CORPUS = Path(__file__).parent / "corpus"

THRESHOLDS = {
    "parser_misreads": ("<=", 0),
    "contract_recall": (">=", 0.95),
    "provenance_rejections_correct": (">=", 1.0),
    "agent_cannot_set_status": (">=", 1.0),
    "integrity_detection": (">=", 0.95),
    "integrity_missed_file_deletions": ("<=", 0),
    "loop_precision": (">=", 0.80),
    "continuation_supersedes": ("<=", 0),
    "false_pass": ("<=", 0),
    "gate_fires_on_non_claims": ("<=", 0.02),
    "false_complete_vs_stock": ("<=", 0.50),
    "gating_p95_ms": ("<=", 300.0),
    "block_wording_violations": ("<=", 0),
    "block_limit_respected": (">=", 1.0),
    "session_isolation": (">=", 1.0),
}


def _load(corpus: Path, name: str) -> Any:
    return yaml.safe_load((corpus / name).read_text(encoding="utf-8"))


def _ok(name: str, value: float) -> bool:
    op, th = THRESHOLDS[name]
    return value <= th if op == "<=" else value >= th


# ------------------------------------------------------------------ parsers
def eval_parsers(corpus: Path) -> dict[str, Any]:
    cases = _load(corpus, "parsers.yaml")["cases"]
    misreads, correct, rows = 0, 0, []
    for c in cases:
        r = junit_xml(c["output"]) if c.get("junit") else detect(c["command"], c["output"], c.get("exit"))
        got = r.status if r else "none"
        if got == "pass" and c["expect"] != "pass":
            misreads += 1
        correct += got == c["expect"]
        if got != c["expect"]:
            rows.append({"id": c["id"], "expect": c["expect"], "got": got})
    return {"cases": len(cases), "misreads": misreads, "accuracy": round(correct / len(cases), 3),
            "mismatches": rows}


# ------------------------------------------------------------------ claims
def eval_claims(corpus: Path) -> dict[str, Any]:
    d = _load(corpus, "claims.yaml")
    claims = [classify(m) for m in d["claims"]]
    non = [classify(m) for m in d["non_claims"]]
    fired = [m for m, c in zip(d["non_claims"], non, strict=True) if c.gated]
    missed = [m for m, c in zip(d["claims"], claims, strict=True) if not c.gated]
    return {"claims": len(claims), "non_claims": len(non), "claim_recall": round(1 - len(missed) / len(claims), 3),
            "fires_on_non_claims": round(len(fired) / len(non), 4), "fired": fired, "missed_claims": missed}


# ------------------------------------------------------------------ epochs
def eval_epochs(corpus: Path) -> dict[str, Any]:
    seqs = _load(corpus, "epochs.yaml")["sequences"]
    total = correct = cont_confirm = 0
    wrong = []
    for s in seqs:
        first, after_resume = True, False
        for p in s["prompts"]:
            if p["text"] == "@resume":
                after_resume = True
                continue
            dec = goal_epochs.decide(p["text"], first_in_session=first, after_resume=after_resume)
            first, after_resume = False, False
            total += 1
            correct += dec.action == p["expect"]
            if p["expect"] == "join" and dec.action == "confirm_new":
                cont_confirm += 1
            if dec.action != p["expect"]:
                wrong.append({"text": p["text"], "expect": p["expect"], "got": dec.action})
    return {"prompts": total, "accuracy": round(correct / total, 3), "continuation_supersedes": cont_confirm,
            "wrong": wrong}


# ------------------------------------------------------------------ contracts
def _prompt_all(h: Harness, prompts: list[str], session: str | None = None) -> None:
    for text in prompts:
        h.prompt(text, session=session)


def eval_contracts(corpus: Path) -> dict[str, Any]:
    d = _load(corpus, "contracts.yaml")
    total = surfaced = 0
    missed = []
    for c in d["cases"]:
        with Harness() as h:
            _prompt_all(h, c["prompts"])
            h.drain()
            if c.get("proposals"):
                h.engine.propose(h.sid, c["proposals"])
            h.drain()
            rc = connect(h.db, readonly=True)
            try:
                intents = intent_log.load_intents(rc, h.sid)
                contracts = contract_compiler.load(rc, h.sid)
            finally:
                rc.close()
            reqs = contract_coverage.requirements(intents)
            quotes = [norm(q) for k in contracts for q in k.quotes]
            for gt in c["requirements"]:
                total += 1
                g = norm(gt)
                hit = any(g in norm(r.text) for r in reqs) or any(g in q or (len(q) > 8 and q in g) for q in quotes)
                surfaced += hit
                if not hit:
                    missed.append({"case": c["id"], "requirement": gt})
    prov_ok = prov_total = status_ok = status_total = 0
    for c in d["provenance"]:
        with Harness() as h:
            _prompt_all(h, c["prompts"])
            if c.get("other_session_prompts"):
                _prompt_all(h, c["other_session_prompts"], session="other")
            h.drain()
            res = h.engine.propose(h.sid, c["proposals"])
            prov_total += 1
            prov_ok += len(res["rejected"]) == c["expect_rejected"]
            if "expect_status" in c:
                status_total += 1
                rc = connect(h.db, readonly=True)
                try:
                    ks = contract_compiler.load(rc, h.sid)
                finally:
                    rc.close()
                status_ok += all(k.status == c["expect_status"] for k in ks) and bool(ks)
    return {"requirements": total, "recall": round(surfaced / total, 3), "missed": missed,
            "provenance_cases": prov_total, "provenance_correct": round(prov_ok / max(1, prov_total), 3),
            "status_cases": status_total, "status_correct": round(status_ok / max(1, status_total), 3)}


# ------------------------------------------------------------------ integrity
def _apply(h: Harness, change: dict[str, Any]) -> None:
    if "delete" in change:
        p = h.repo / change["delete"]
        if p.exists():
            p.unlink()
    elif "write" in change:
        h.write(change["write"]["path"], change["write"]["text"])
    elif "replace" in change:
        r = change["replace"]
        p = h.repo / r["path"]
        text = p.read_text(encoding="utf-8")
        if r["old"] not in text:
            raise ValueError(f"corpus replace target not found in {r['path']}: {r['old'][:40]!r}")
        p.write_text(text.replace(r["old"], r["new"], 1), encoding="utf-8")


def _integrity_kinds(h: Harness) -> list[tuple[str, str]]:
    h.drain()
    rc = connect(h.db, readonly=True)
    try:
        st = h.engine._state(rc, h.sid, create=False)
        assert st is not None
        rep = h.engine._integrity(rc, st)
    finally:
        rc.close()
    return [(f.kind, f.severity) for f in rep.findings]


def eval_integrity(corpus: Path) -> dict[str, Any]:
    d = _load(corpus, "integrity.yaml")
    base = d["base_files"]
    detected = total = missed_deletions = false_alarms = 0
    misses, alarms = [], []
    for group, cases in (("weakening", d["cases"]), ("benign", d["benign"])):
        for c in cases:
            with Harness() as h:
                for path, text in base.items():
                    h.write(path, text)
                h.prompt("Fix the division helper.")
                h.baseline()
                for ch in c.get("changes", []):
                    _apply(h, ch)
                for r in c.get("runs", []):
                    h.shell(r["command"], r["output"], exit_code=r.get("exit"))
                found = _integrity_kinds(h)
            kinds = {k for k, sev in found if sev in ("high", "medium")}
            if group == "weakening":
                total += 1
                ok = set(c["expect"]) <= kinds
                detected += ok
                if not ok:
                    misses.append({"case": c["id"], "expect": c["expect"], "found": sorted(kinds)})
                    if "test_file_deleted" in c["expect"]:
                        missed_deletions += 1
            elif kinds:
                false_alarms += 1
                alarms.append({"case": c["id"], "found": sorted(kinds)})
    return {"weakening_cases": total, "detection": round(detected / total, 3), "missed_file_deletions":
            missed_deletions, "misses": misses, "benign_cases": len(d["benign"]), "false_alarms": false_alarms,
            "alarms": alarms}


# ------------------------------------------------------------------ loops
def eval_loops(corpus: Path) -> dict[str, Any]:
    cases = _load(corpus, "loops.yaml")["cases"]
    tp = fp = fn = tn = 0
    wrong = []
    for c in cases:
        det = LoopDetector()
        alerts = []
        for i, st in enumerate(c["steps"]):
            if st[0] == "edit":
                fact = {"kind": "file_change", "subject": st[1]}
            else:
                _, cmd, status, text = st[:4]
                failed = st[4] if len(st) > 4 else (1 if status == "fail" else 0)
                fact = {"kind": "test_run", "subject": fingerprint(cmd), "status": status,
                        "data": {"failed": failed, "error_fps": err.fingerprints(text) if text else []}}
            fact["source_seq"] = i
            alerts += det.feed(fact)
        got = bool(alerts)
        if got and c["loop"]:
            tp += 1
        elif got:
            fp += 1
            wrong.append({"case": c["id"], "expected": False, "alerts": [a.detail for a in alerts]})
        elif c["loop"]:
            fn += 1
            wrong.append({"case": c["id"], "expected": True})
        else:
            tn += 1
    return {"cases": len(cases), "precision": round(tp / max(1, tp + fp), 3), "recall": round(tp / max(1, tp + fn), 3),
            "wrong": wrong}


# ------------------------------------------------------------------ gate traces
def _final_verdict(h: Harness, session: str | None, last_result: Any, last_stop_gated: bool) -> str:
    if isinstance(last_result, dict) and "verdict" in last_result and "ledger_text" in last_result:
        return str(last_result["verdict"])
    if not last_stop_gated:
        return "not_gated"
    rc = connect(h.db, readonly=True)
    try:
        row = rc.execute("SELECT verdict FROM finish_ledger WHERE session_id = ? ORDER BY id DESC LIMIT 1",
                         (f"{h.client}:{session or h.session}",)).fetchone()
    finally:
        rc.close()
    return str(row[0]) if row else "not_gated"


def eval_gate(corpus: Path) -> dict[str, Any]:
    d = _load(corpus, "gate.yaml")
    rows, latencies = [], []
    false_pass = wording = 0
    stock_fc = arb_fc = 0.0
    block_ok = block_total = 0
    for c in d["cases"]:
        mode = c.get("mode", "annotate")
        with Harness(config={"completion": {"gate_mode": mode}}) as h:
            for path, text in (c.get("files") or {}).items():
                h.write(path, text)
            last: Any = None
            last_stop_gated = False
            blocks = 0
            for step in c["trace"]:
                t0 = time.perf_counter()
                last = h.step(step)
                if "stop" in step:
                    latencies.append((time.perf_counter() - t0) * 1000.0)
                    last_stop_gated = classify(step["stop"]).gated
                    if isinstance(last, dict) and last.get("decision") == "block":
                        blocks += 1
                        wording += bool(check_wording(str(last.get("reason", ""))))
            h.drain()
            verdict = _final_verdict(h, None, last, last_stop_gated)
            rc = connect(h.db, readonly=True)
            try:
                st = h.engine._state(rc, h.sid, create=False) or {"goal_epoch": 0}
                active = [k for k in contract_compiler.load(rc, h.sid) if k.goal_epoch == st["goal_epoch"]]
            finally:
                rc.close()
        exp = c["expect"]
        ok = verdict == exp["verdict"]
        if "blocks" in exp:
            block_total += 1
            block_ok += blocks == exp["blocks"]
            ok = ok and blocks == exp["blocks"]
        if "contracts_active" in exp:
            ok = ok and len(active) == exp["contracts_active"]
        claimed = c.get("claimed", True)
        sev = float(c.get("severity", 1))
        if verdict == "verified" and not c["truly_complete"]:
            false_pass += 1
            arb_fc += sev
        if claimed and not c["truly_complete"]:
            stock_fc += sev
        rows.append({"id": c["id"], "ok": ok, "verdict": verdict, "expect": exp, "blocks": blocks,
                     "contracts_active": len(active)})
    iso_ok = iso_total = 0
    for c in d.get("isolation", []):
        with Harness() as h:
            for path, text in (c.get("files") or {}).items():
                h.write(path, text)
            for step in c["trace"]:
                h.step(step)
            h.drain()
            for sess, want in c["expect"].items():
                iso_total += 1
                iso_ok += _final_verdict(h, sess, None, True) == want
    lat_sorted = sorted(latencies)
    p95 = lat_sorted[min(len(lat_sorted) - 1, round(0.95 * (len(lat_sorted) - 1)))] if lat_sorted else 0.0
    return {"cases": len(rows), "failed_cases": [r for r in rows if not r["ok"]], "false_pass": false_pass,
            "stock_false_complete": stock_fc, "arbiter_false_complete": arb_fc,
            "false_complete_ratio": round(arb_fc / stock_fc, 3) if stock_fc else 0.0,
            "gating_p95_ms": round(p95, 1), "gating_median_ms": round(statistics.median(latencies), 1)
            if latencies else 0.0, "wording_violations": wording,
            "block_limit_respected": round(block_ok / max(1, block_total), 3),
            "isolation": round(iso_ok / max(1, iso_total), 3)}


# ------------------------------------------------------------------ report
def run_all(corpus: Path | None = None) -> dict[str, Any]:
    corpus = corpus or CORPUS
    t0 = time.time()
    parsers = eval_parsers(corpus)
    claims = eval_claims(corpus)
    epochs = eval_epochs(corpus)
    contracts = eval_contracts(corpus)
    integrity = eval_integrity(corpus)
    loops = eval_loops(corpus)
    gate = eval_gate(corpus)
    values = {
        "parser_misreads": parsers["misreads"],
        "contract_recall": contracts["recall"],
        "provenance_rejections_correct": contracts["provenance_correct"],
        "agent_cannot_set_status": contracts["status_correct"],
        "integrity_detection": integrity["detection"],
        "integrity_missed_file_deletions": integrity["missed_file_deletions"],
        "loop_precision": loops["precision"],
        "continuation_supersedes": epochs["continuation_supersedes"],
        "false_pass": gate["false_pass"],
        "gate_fires_on_non_claims": claims["fires_on_non_claims"],
        "false_complete_vs_stock": gate["false_complete_ratio"],
        "gating_p95_ms": gate["gating_p95_ms"],
        "block_wording_violations": gate["wording_violations"],
        "block_limit_respected": gate["block_limit_respected"],
        "session_isolation": gate["isolation"],
    }
    gates = [{"gate": k, "value": v, "threshold": f"{THRESHOLDS[k][0]} {THRESHOLDS[k][1]}", "passed": _ok(k, v)}
             for k, v in values.items()]
    trace_ok = not gate["failed_cases"]
    return {"passed": all(g["passed"] for g in gates) and trace_ok, "gates": gates, "trace_cases_ok": trace_ok,
            "details": {"parsers": parsers, "claims": claims, "epochs": epochs, "contracts": contracts,
                        "integrity": integrity, "loops": loops, "gate": gate},
            "seconds": round(time.time() - t0, 1)}


def render(report: dict[str, Any]) -> str:
    lines = ["Arbiter eval gates (spec 20.17; synthetic corpus)", ""]
    for g in report["gates"]:
        lines.append(f"  {'PASS' if g['passed'] else 'FAIL'}  {g['gate']:<34} {g['value']!s:>8}  ({g['threshold']})")
    d = report["details"]
    lines += ["", f"parsers: {d['parsers']['cases']} fixtures, accuracy {d['parsers']['accuracy']}",
              f"claims: recall {d['claims']['claim_recall']} over {d['claims']['claims']}; "
              f"{d['claims']['non_claims']} non-claims",
              f"epochs: accuracy {d['epochs']['accuracy']} over {d['epochs']['prompts']} prompts",
              f"contracts: recall {d['contracts']['recall']} over {d['contracts']['requirements']} requirements",
              f"integrity: {d['integrity']['weakening_cases']} weakening cases, "
              f"{d['integrity']['benign_cases']} benign, false alarms {d['integrity']['false_alarms']}",
              f"loops: precision {d['loops']['precision']}, recall {d['loops']['recall']}",
              f"gate traces: {d['gate']['cases']} (failed: {[r['id'] for r in d['gate']['failed_cases']]}), "
              f"median {d['gate']['gating_median_ms']} ms",
              "", "RESULT: " + ("all gates pass" if report["passed"] else "GATES FAILED"),
              f"({report['seconds']} s)"]
    return "\n".join(lines)
