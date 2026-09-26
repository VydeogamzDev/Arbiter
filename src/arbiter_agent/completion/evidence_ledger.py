"""Finish ledger (spec §12.5) and verdict.

The verdict is "verified to the configured evidence standard", never "correct":
- every active contract is PASS or WAIVED (UNKNOWN never counts as PASS);
- there is at least one contract (nothing to check means unverified), or, in the default
  ``completion.evidence_mode: observed``, Arbiter observed a passing test run after the last
  code change (and something was done for this task at all);
- when contracts exist, no requirement-like intent in the active epoch is left uncovered;
- test-based PASS evidence is only counted when test integrity is OK against a baseline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from arbiter_agent.completion.evidence_grades import ContractEval
from arbiter_agent.state.contract_compiler import is_runner_command
from arbiter_agent.state.contract_coverage import Requirement
from arbiter_agent.state.schema import Contract
from arbiter_agent.telemetry.test_integrity import IntegrityReport

MAX_LIST = 8


@dataclass
class Ledger:
    session_id: str
    goal_epoch: int
    verdict: str                               # verified | unverified
    contracts: list[tuple[Contract, ContractEval]] = field(default_factory=list)
    uncovered: list[Requirement] = field(default_factory=list)
    integrity: IntegrityReport | None = None
    tests: list[dict[str, Any]] = field(default_factory=list)
    broader: list[dict[str, Any]] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    loop_alerts: list[dict[str, Any]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out = {"pass": 0, "fail": 0, "unknown": 0, "waived": 0}
        for _, ev in self.contracts:
            out[ev.status] = out.get(ev.status, 0) + 1
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id, "goal_epoch": self.goal_epoch, "verdict": self.verdict,
            "counts": self.counts(),
            "contracts": [{"id": c.id, "text": c.text, "recipe": c.recipe.describe(), "status": ev.status,
                           "grade": ev.grade, "reason": ev.reason, "strength": c.strength_flag,
                           "proposed_by": c.proposed_by} for c, ev in self.contracts],
            "uncovered": [r.to_dict() for r in self.uncovered],
            "integrity": self.integrity.to_dict() if self.integrity else None,
            "tests": self.tests, "broader": self.broader, "unresolved": self.unresolved,
            "loop_alerts": self.loop_alerts, "missing": self.missing, "notes": self.notes,
        }

    def text(self) -> str:
        c = self.counts()
        lines = [f"Goal epoch: {self.goal_epoch}",
                 "Objective: " + ("satisfied to configured evidence standard" if self.verdict == "verified"
                                  else "NOT verified to the configured evidence standard"),
                 f"Contracts: {c['pass']} PASS / {c['fail']} FAIL / {c['unknown']} UNKNOWN"
                 + (f" / {c['waived']} WAIVED" if c["waived"] else "")]
        for con, ev in self.contracts[:12]:
            flag = " [low-strength]" if con.strength_flag == "low" else ""
            lines.append(f"  {con.id} {ev.status.upper()}{flag}: {con.text[:90]} - {ev.reason}"[:200])
        if self.uncovered:
            lines.append(f"Uncovered requests: {len(self.uncovered)}")
            lines += [f"  {r.intent_id}: \"{r.text[:110]}\"" for r in self.uncovered[:MAX_LIST]]
        if self.tests:
            lines.append("Targeted tests: " + "; ".join(_run_str(t) for t in self.tests[:MAX_LIST]))
        if self.broader:
            lines.append("Broader verification: " + "; ".join(_run_str(t) for t in self.broader[:MAX_LIST]))
        lines.append("Test integrity: " + (self.integrity.summary() if self.integrity else "not checked"))
        if self.loop_alerts:
            lines.append("Loop alerts: " + "; ".join(a.get("detail", "")[:100] for a in self.loop_alerts[:3]))
        lines.append("Known unresolved issues: " + ("; ".join(self.unresolved[:MAX_LIST]) if self.unresolved else
                                                    "none observed"))
        lines += self.notes
        return "\n".join(lines)


def _run_str(t: dict[str, Any]) -> str:
    total = t.get("total")
    counts = f" {t.get('passed', 0)}/{total}" if isinstance(total, int) and total else ""
    return f"`{str(t.get('subject'))[:60]}`{counts} {str(t.get('status')).upper()}"


def latest_runs(facts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    latest: dict[str, dict[str, Any]] = {}
    for f in facts:
        if f["kind"] == "test_run":
            latest[str(f["subject"])] = f
    tests = [f for f in latest.values() if f["data"].get("runner_kind") == "test"]
    broader = [f for f in latest.values() if f["data"].get("runner_kind") in ("typecheck", "lint", "build")]
    unresolved = [f"`{str(f['subject'])[:60]}` last failed" for f in latest.values() if f["status"] == "fail"]
    for f in latest.values():
        if f["status"] == "fail":
            for _, text in (f["data"].get("error_fps") or [])[:1]:
                unresolved.append(text[:100])
    return ([_slim(f) for f in tests], [_slim(f) for f in broader], unresolved)


def _slim(f: dict[str, Any]) -> dict[str, Any]:
    return {"subject": f["subject"], "status": f["status"], "passed": f["data"].get("passed"),
            "failed": f["data"].get("failed"), "total": f["data"].get("total"), "runner": f["data"].get("runner")}


def observed_gap(facts: list[dict[str, Any]], epoch: int, change_seq: int) -> str | None:
    """What's missing from Arbiter's own observations for this task (None: nothing): a passing
    test run after the last code change, no failing latest run, and something done at all."""
    fresh = [x for x in facts if x["kind"] == "test_run" and x["data"].get("runner_kind") == "test"
             and int(x.get("source_seq") or 0) >= change_seq]
    latest: dict[str, dict[str, Any]] = {}
    for x in fresh:
        latest[str(x["subject"])] = x
    if not any(x.get("goal_epoch") == epoch and x["kind"] in ("file_change", "test_run") for x in facts):
        return "no file changes or test runs were observed for this task"
    if change_seq and not fresh:
        return "no test run observed after the last code change"
    if any(x["status"] == "fail" for x in latest.values()):
        return "the latest test run after the last code change failed"
    if change_seq and not any(x["status"] == "pass" for x in latest.values()):
        return "no passing test run observed after the last code change"
    return None


def build(session_id: str, epoch: int, evals: list[tuple[Contract, ContractEval]], uncovered: list[Requirement],
          integrity: IntegrityReport | None, facts: list[dict[str, Any]],
          loop_alerts: list[dict[str, Any]] | None = None, *, flag_uncovered: bool = True,
          evidence_mode: str = "contracts", change_seq: int = 0) -> Ledger:
    tests, broader, unresolved = latest_runs(facts)
    missing: list[str] = []
    notes: list[str] = []
    uses_tests = False
    # Evidence-first (benchmark finding, 2026-09-25): in observed mode, what Arbiter itself saw
    # decides when no contracts exist, so the agent isn't sent back just to write contracts.
    # Contracts, once recorded, keep their full rules (uncovered requests included).
    obs_gap = observed_gap(facts, epoch, change_seq) if evidence_mode == "observed" else "n/a"
    for c, ev in evals:
        if ev.status == "pass" and c.strength_flag == "low" and obs_gap is not None:
            # Weak-contract defense (6.8.1 step 7): a trivially satisfiable recipe can't carry a
            # verified verdict on its own (a fresh passing test run beside it can); the user can waive it.
            missing.append(f"{c.id} ({c.recipe.describe()[:80]}): PASS, but low-strength - {c.strength_reason}"[:220])
            continue
        if ev.status in ("pass", "waived"):
            if ev.status == "pass" and (c.recipe.type == "test_command" or (
                    c.recipe.type == "command_exit" and is_runner_command(str(c.recipe.params.get("command"))))):
                uses_tests = True
            continue
        what = c.recipe.describe()
        missing.append(f"{c.id} ({what[:80]}): {ev.status.upper()} - {ev.reason}"[:220])
    if not evals and evidence_mode == "observed":
        if obs_gap:
            missing.append(obs_gap)
        elif change_seq:
            uses_tests = True
            notes.append("Observed evidence: tests passed after the last code change.")
    elif not evals:
        missing.append("no contracts are recorded for this goal epoch, so there is nothing to verify against")
    if flag_uncovered and (evals or evidence_mode != "observed"):
        for r in uncovered[:MAX_LIST]:
            missing.append(f"request {r.intent_id} has no contract: \"{r.text[:100]}\"")
    if uses_tests and integrity is not None and integrity.status != "ok":
        if integrity.status == "unknown":
            missing.append(f"test integrity is UNKNOWN ({integrity.reason}); test results can't be counted as PASS")
        else:
            for f in integrity.findings:
                if f.severity in ("high", "medium"):
                    missing.append(f"test integrity: {f.kind.replace('_', ' ')} in {f.path} ({f.detail})"[:200])
                    if len(missing) > 12:
                        break
    low = [c.id for c, _ in evals if c.strength_flag == "low"]
    if low:
        notes.append(f"Low-strength contracts: {', '.join(low)} (recipe weaker than the quoted request)")
    verdict = "verified" if not missing else "unverified"
    return Ledger(session_id, epoch, verdict, evals, uncovered if flag_uncovered else [], integrity, tests, broader,
                  unresolved, loop_alerts or [], missing, notes)
