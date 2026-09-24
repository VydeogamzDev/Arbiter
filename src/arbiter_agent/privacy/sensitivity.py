"""Sensitivity classes attached to stored evidence (spec §16.2 ``sensitivity_class``)."""

NORMAL = "normal"
REDACTED = "redacted"  # at least one secret was replaced at ingest
TRUNCATED = "truncated"  # payload exceeded storage.max_payload_kb
# Excluded sessions are never stored; only a counter is kept (spec §16.3.1).

ALL = (NORMAL, REDACTED, TRUNCATED)


def classify(redactions: int, truncated: bool) -> str:
    if redactions:
        return REDACTED
    if truncated:
        return TRUNCATED
    return NORMAL
