"""Optional embeddings channel (spec §11.2, M6.5). Off by default.

The interface is here so a vector channel can join candidate generation later (for example a
small CPU embedding model, or the M7 sensor backends). The default backend returns nothing, so
retrieval stays lexical + structural; it never "searches the repository" on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class EmbeddingBackend(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[list[float]] | None: ...


@dataclass
class NullEmbeddings:
    name: str = "off"

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        return None


def backend_from_config(config: Any) -> EmbeddingBackend:
    choice = str(config.get("retrieval.embeddings", "off") or "off")
    if choice != "off":
        # Only "off" ships in M6; unknown values fall back rather than half-enabling a channel.
        return NullEmbeddings(name=f"off (unsupported: {choice})")
    return NullEmbeddings()
