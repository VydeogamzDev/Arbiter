"""Test-integrity checks against the session baseline (spec §12.4).

Findings are deterministic and conservative: anything that could make tests easier to pass
is reported. Without a baseline the status is UNKNOWN, never OK.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from arbiter_agent.state.repo_identity import RepoIdentity
from arbiter_agent.telemetry.baseline import Baseline, FileMetrics, scan

HIGH, MEDIUM, LOW = "high", "medium", "low"


@dataclass
class Finding:
    kind: str
    path: str
    severity: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IntegrityReport:
    status: str                       # ok | alert | unknown
    findings: list[Finding] = field(default_factory=list)
    reason: str = ""
    files_checked: int = 0

    @property
    def high(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == HIGH]

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "reason": self.reason, "files_checked": self.files_checked,
                "findings": [f.to_dict() for f in self.findings]}

    def summary(self) -> str:
        if self.status == "unknown":
            return f"UNKNOWN ({self.reason or 'no baseline'})"
        if not self.findings:
            return "no weakening detected"
        kinds: dict[str, int] = {}
        for f in self.findings:
            kinds[f.kind] = kinds.get(f.kind, 0) + 1
        return "ALERT: " + ", ".join(f"{k.replace('_', ' ')} x{n}" for k, n in sorted(kinds.items()))


def compare(base: dict[str, FileMetrics], cur: dict[str, FileMetrics], *, base_truncated: bool = False,
            cur_truncated: bool = False) -> list[Finding]:
    out: list[Finding] = []
    for key, b in sorted(base.items()):
        c = cur.get(key)
        if c is None:
            if cur_truncated:
                continue
            if b.role == "test":
                out.append(Finding("test_file_deleted", key, HIGH, f"{b.tests} test(s) removed with the file"))
            elif b.role == "fixture":
                out.append(Finding("fixture_deleted", key, MEDIUM, "fixture/snapshot file removed"))
            else:
                out.append(Finding("harness_file_deleted", key, HIGH, "runner/harness configuration removed"))
            continue
        if c.sha == b.sha:
            continue
        if b.role == "test" or (b.role == "harness" and key.endswith("conftest.py")):
            if c.tests < b.tests:
                out.append(Finding("tests_removed", key, HIGH, f"test definitions {b.tests} -> {c.tests}"))
            if c.skips > b.skips:
                out.append(Finding("skip_added", key, HIGH, f"skip/focus markers {b.skips} -> {c.skips}"))
            if c.asserts < b.asserts:
                sev = HIGH if c.tests >= b.tests else MEDIUM
                out.append(Finding("assertions_reduced", key, sev, f"assertions {b.asserts} -> {c.asserts}"))
        if b.role == "fixture":
            out.append(Finding("fixture_rewritten", key, MEDIUM, "fixture/snapshot/golden content changed"))
        if b.role == "harness":
            if c.filters > b.filters:
                out.append(Finding("harness_filter_added", key, HIGH,
                                   f"test selection/exclusion/threshold settings {b.filters} -> {c.filters}"))
            else:
                out.append(Finding("harness_changed", key, LOW, "runner/harness configuration changed"))
    if not base_truncated:
        for key, c in sorted(cur.items()):
            if key in base:
                continue
            if c.role == "test" and c.skips:
                out.append(Finding("skip_added", key, MEDIUM, f"new test file with {c.skips} skip/focus marker(s)"))
            elif c.role == "fixture" and ("snap" in key or "golden" in key):
                out.append(Finding("snapshot_added", key, LOW, "new snapshot/golden file"))
    return out


def runner_regressions(runs: list[dict[str, Any]]) -> list[Finding]:
    """Fewer tests executed by the same command later in the session (filters, deletions)."""
    out: list[Finding] = []
    best: dict[str, int] = {}
    for r in runs:
        fp, total = r.get("subject"), r.get("total")
        if not fp or not isinstance(total, int) or total <= 0:
            continue
        prev = best.get(fp)
        if prev is not None and total < prev:
            out.append(Finding("fewer_tests_ran", str(fp), HIGH, f"`{fp}` ran {total} test(s), earlier {prev}"))
        best[fp] = max(prev or 0, total)
    return out


def check(baseline: Baseline | None, ident: RepoIdentity | None, runs: list[dict[str, Any]] | None = None,
          acknowledged: set[str] | None = None, max_s: float | None = None) -> IntegrityReport:
    if baseline is None or ident is None:
        return IntegrityReport("unknown", reason="no session baseline")
    cur, truncated = scan(ident, max_s)
    if truncated and len(cur) < 5000:
        return IntegrityReport("unknown", reason="integrity scan ran out of time", files_checked=len(cur))
    findings = compare(baseline.files, cur, base_truncated=baseline.truncated, cur_truncated=truncated)
    findings += runner_regressions(runs or [])
    ack = acknowledged or set()
    findings = [f for f in findings if f"{f.kind}:{f.path}" not in ack]
    status = "alert" if any(f.severity in (HIGH, MEDIUM) for f in findings) else "ok"
    return IntegrityReport(status, findings, files_checked=len(cur))
