"""Error fingerprints: the same failure repeated with different numbers, paths or addresses
maps to one fingerprint, which feeds loop detection and "same error occurrences"."""

from __future__ import annotations

import hashlib
import re

_LINE_PATTERNS = [
    re.compile(r"^(?:E\s+)?([A-Z][\w.]*(?:Error|Exception|Failure|Warning)\b.*)$"),   # Python, JS, Java
    re.compile(r"^(error(?:\[E\d+\])?:.*)$", re.I),                                    # rustc, gcc, generic
    re.compile(r"^(.*error TS\d+:.*)$"),                                                # tsc
    re.compile(r"^(FAILED\s+\S+.*)$"),                                                  # pytest short summary
    re.compile(r"^(\s*--- FAIL:.*)$"),                                                  # go
    re.compile(r"^(panic:.*)$"),                                                        # go/rust panics
    re.compile(r"^(.*(?:is not recognized as|command not found|No such file or directory).*)$"),
    re.compile(r"^(\s*●\s.+)$"),                                                        # jest failure header
]
_NORMALIZERS = [
    (re.compile(r"0x[0-9a-fA-F]+"), "0x?"),
    (re.compile(r"(?:[A-Za-z]:)?[\\/](?:[\w.\- ]+[\\/])+"), "<path>/"),
    (re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|s|sec|seconds)\b"), "<dur>"),
    (re.compile(r"\bline \d+\b"), "line <n>"),
    (re.compile(r":\d+(?::\d+)?\b"), ":<n>"),
    (re.compile(r"\b\d+\b"), "<n>"),
    (re.compile(r"'[^']{40,}'|\"[^\"]{40,}\""), "<str>"),
    (re.compile(r"\s+"), " "),
]
MAX_ERRORS = 5


def extract(output: str) -> list[str]:
    """Distinct error lines (most specific first), capped."""
    seen: list[str] = []
    for line in (output or "").splitlines()[-400:]:
        line = line.rstrip()
        if len(line) > 400:
            line = line[:400]
        for pat in _LINE_PATTERNS:
            m = pat.match(line)
            if m:
                s = m.group(1).strip()
                if s and s not in seen:
                    seen.append(s)
                break
    return seen[-MAX_ERRORS:]


def normalize(line: str) -> str:
    s = line
    for pat, rep in _NORMALIZERS:
        s = pat.sub(rep, s)
    return s.strip()[:240]


def fingerprint(line: str) -> str:
    return hashlib.sha256(normalize(line).encode("utf-8")).hexdigest()[:16]


def fingerprints(output: str) -> list[tuple[str, str]]:
    """[(fingerprint, normalized line)] for the error lines in ``output``."""
    out: list[tuple[str, str]] = []
    for line in extract(output):
        n = normalize(line)
        fp = hashlib.sha256(n.encode("utf-8")).hexdigest()[:16]
        if all(fp != f for f, _ in out):
            out.append((fp, n))
    return out
