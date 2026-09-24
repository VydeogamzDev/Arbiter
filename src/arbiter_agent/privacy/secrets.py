"""Secret detectors (spec §16.3.2). Each detector yields (kind, start, end) spans of the
secret *value*; surrounding context (key names, 'Bearer') is kept for readability."""

from __future__ import annotations

import math
import re
from collections.abc import Iterator
from dataclasses import dataclass

SECRET_KEY_NAMES = re.compile(
    r"(?i)(password|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|private[_-]?key|client[_-]?secret|"
    r"authorization|auth[_-]?token|session[_-]?key|credentials?)")


@dataclass(frozen=True)
class Detector:
    kind: str
    pattern: re.Pattern[str]
    group: int = 0  # which regex group holds the secret value


DETECTORS: list[Detector] = [
    Detector("private_key", re.compile(
        r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z]+ )?PRIVATE KEY-----")),
    Detector("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    Detector("openai_key", re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}")),
    Detector("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})")),
    Detector("aws_access_key_id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    Detector("aws_secret_access_key", re.compile(
        r"(?i)aws_secret_access_key\s*[=:]\s*[\"']?([A-Za-z0-9/+=]{40})"), group=1),
    Detector("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),
    Detector("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}")),
    Detector("stripe_key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{16,}")),
    Detector("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    Detector("bearer_token", re.compile(r"(?i)\bbearer\s+([A-Za-z0-9._~+/\-]{16,}=*)"), group=1),
    Detector("url_password", re.compile(r"[a-z][a-z0-9+.\-]*://[^/\s:@]+:([^@\s/]{3,})@"), group=1),
    Detector("env_assignment", re.compile(
        r"(?m)^\s*(?:export\s+|set\s+|\$env:)?[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|APIKEY|PRIVATE_KEY|"
        r"ACCESS_KEY|AUTH|CREDENTIAL)[A-Z0-9_]*\s*[=:]\s*[\"']?([^\s\"'#]{6,})"), group=1),
    Detector("json_secret_field", re.compile(
        r"(?i)\"(?:password|passwd|secret|token|api_?key|access_?token|refresh_?token|client_?secret|private_?key)\""
        r"\s*:\s*\"([^\"]{6,})\""), group=1),
]


def shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def looks_high_entropy(value: str) -> bool:
    """Heuristic for credential-like values found under secret-looking key names."""
    return len(value) >= 12 and shannon_entropy(value) >= 3.3 and not value.isalpha()


def find_secrets(text: str) -> Iterator[tuple[str, int, int]]:
    spans: list[tuple[str, int, int]] = []
    for det in DETECTORS:
        for m in det.pattern.finditer(text):
            start, end = m.span(det.group)
            if start < 0 or end <= start:
                continue
            spans.append((det.kind, start, end))
    # Drop spans contained in an earlier, larger span (e.g. openai_key inside env_assignment).
    spans.sort(key=lambda s: (s[1], -(s[2] - s[1])))
    last_end = -1
    for kind, start, end in spans:
        if start < last_end:
            continue
        last_end = end
        yield kind, start, end
