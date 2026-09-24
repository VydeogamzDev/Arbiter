"""Text normalization shared by quote provenance and coverage checks."""

from __future__ import annotations

import re
import unicodedata

_QUOTES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-",
                         "\u2014": "-", "\u00a0": " ", "`": "'"})


def norm(text: str) -> str:
    """Casefold, NFC, straight quotes, collapsed whitespace. Punctuation is kept."""
    t = unicodedata.normalize("NFC", text or "").translate(_QUOTES).casefold()
    return re.sub(r"\s+", " ", t).strip()


def norm_loose(text: str) -> str:
    """Like :func:`norm` but also drops punctuation other than path/identifier characters."""
    t = norm(text)
    t = re.sub(r"[^\w\s./\:@#-]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def words(text: str) -> list[str]:
    return re.findall(r"[\w./-]+", norm(text))


def sentences(text: str) -> list[str]:
    """Split user text into requirement-sized pieces: sentences, bullets and numbered items."""
    out: list[str] = []
    for block in re.split(r"\n\s*\n|\n(?=\s*(?:[-*•]|\d+[.)])\s)", text or ""):
        block = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", block.strip())
        if not block:
            continue
        # split on sentence ends not inside paths/versions (a '.' followed by space + capital/quote)
        parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'`(\[])|\n+|;\s+", block)
        out.extend(p.strip() for p in parts if p and p.strip())
    return out
