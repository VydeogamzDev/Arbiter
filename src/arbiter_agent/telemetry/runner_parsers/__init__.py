"""Test/build/lint output parsers (spec §12.8).

Rules that keep misreads at zero:
- PASS only from an explicit success summary (e.g. ``3 passed in 0.2s``, ``Tests: 4 passed``,
  ``Success: no issues found``). Absence of failures is never a pass.
- If a known non-zero exit code contradicts a parsed PASS, the result is UNKNOWN (and counts
  toward the parser circuit breaker).
- Unrecognized output returns ``None``: it stays raw evidence, never a verdict.
"""

from __future__ import annotations

from arbiter_agent.telemetry.runner_parsers.base import RunnerResult, fingerprint, strip_wrappers
from arbiter_agent.telemetry.runner_parsers.registry import PARSERS, detect, junit_xml

__all__ = ["PARSERS", "RunnerResult", "detect", "fingerprint", "junit_xml", "strip_wrappers"]
