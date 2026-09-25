"""Effort/model update confirmation (spec §8.5).

Where Arbiter (or a host) applies a recommendation, the result is recorded as Applied, Rejected or
TargetUnavailable, and the next call's cache metrics are compared. If changing effort destroys the
prompt cache on this build, dynamic changes are switched off for the session
(``reasoning.disable_dynamic_effort_on_cache_regression``). In hosted clients Arbiter only advises,
so this is fed by hosts through ``report_outcome``.
"""

from __future__ import annotations

from dataclasses import dataclass

STATUSES = ("applied", "rejected", "target_unavailable")
CACHE_REGRESSION = 0.5        # cached share of input dropping by half after a change = regression


@dataclass
class UpdateCheck:
    status: str
    cache_regression: bool
    note: str


def check(status: str, cached_before: float | None, cached_after: float | None) -> UpdateCheck:
    if status not in STATUSES:
        return UpdateCheck("unknown", False, f"unknown update status {status!r}")
    if status != "applied" or cached_before is None or cached_after is None or cached_before <= 0:
        return UpdateCheck(status, False, "no cache comparison")
    regressed = cached_after < cached_before * CACHE_REGRESSION
    return UpdateCheck(status, regressed, "cached share fell after the change" if regressed else "cache preserved")
