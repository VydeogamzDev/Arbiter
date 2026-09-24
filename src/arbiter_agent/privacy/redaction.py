"""Redaction at ingest (spec §16.3.2).

Secrets are replaced with ``[REDACTED:<kind>:<hmac8>]``. The HMAC uses a per-install key, so
repeated occurrences of the same secret correlate without the value ever being stored, and a
guessable secret can't be confirmed from the placeholder alone.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from arbiter_agent.privacy.secrets import SECRET_KEY_NAMES, find_secrets, looks_high_entropy


def load_install_key(path: Path) -> bytes:
    """Per-install 32-byte key for keyed identifiers; created on first use, user-only."""
    if path.exists():
        data = path.read_bytes()
        if len(data) >= 32:
            return data[:32]
    from arbiter_agent.paths import write_private

    key = os.urandom(32)
    write_private(path, key)
    return key


@dataclass
class Redactor:
    key: bytes
    enabled: bool = True
    counts: dict[str, int] = field(default_factory=dict)

    def keyed_id(self, value: str | bytes, n: int = 8) -> str:
        raw = value.encode("utf-8", "surrogatepass") if isinstance(value, str) else value
        return hmac.new(self.key, raw, hashlib.sha256).hexdigest()[:n]

    def _placeholder(self, kind: str, secret: str) -> str:
        self.counts[kind] = self.counts.get(kind, 0) + 1
        return f"[REDACTED:{kind}:{self.keyed_id(secret)}]"

    def redact_text(self, text: str) -> str:
        if not self.enabled or not text:
            return text
        out, pos = [], 0
        for kind, start, end in find_secrets(text):
            out.append(text[pos:start])
            out.append(self._placeholder(kind, text[start:end]))
            pos = end
        if pos == 0:
            return text
        out.append(text[pos:])
        return "".join(out)

    def redact(self, obj: Any, _key: str | None = None) -> Any:
        """Recursively redact strings in JSON-like data. Values under secret-looking keys
        are redacted when they look credential-like even if no pattern matches."""
        if not self.enabled:
            return obj
        if isinstance(obj, str):
            if _key is not None and SECRET_KEY_NAMES.search(_key) and looks_high_entropy(obj):
                return self._placeholder("keyed_field", obj)
            return self.redact_text(obj)
        if isinstance(obj, dict):
            return {k: self.redact(v, str(k)) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.redact(v) for v in obj]
        return obj

    @property
    def total(self) -> int:
        return sum(self.counts.values())
