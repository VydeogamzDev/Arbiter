"""Sensor backends (spec §7.8, §7.9; decision 0026)."""

from __future__ import annotations

from typing import Any

from arbiter_agent.semif.backends.base import Backend, ScoreRequest
from arbiter_agent.semif.backends.null import NullBackend


def from_config(config: Any, stage: str = "decoder") -> Backend:
    """The backend for a stage (``encoder`` = tier 0, ``decoder`` = tiers 1+). Anything that can't
    be constructed (missing package, no server) degrades to the null backend."""
    if not config.get("semif.enabled", False) and stage == "decoder":
        return NullBackend(reason="sensor disabled (arbiter semif enable)")
    try:
        if stage == "encoder":
            enc = config.get("semif.encoder") or {}
            if not enc.get("enabled"):
                return NullBackend(reason="tier 0 encoder disabled")
            from arbiter_agent.semif.backends.encoder import EncoderBackend

            return EncoderBackend(model_id=str(enc.get("model", "fastino/GLiNER2.5-Decide")),
                                  device=str(enc.get("device", "auto")), revision=enc.get("revision"))
        name = str(config.get("semif.backend", "auto"))
        if name in ("auto", "llama_cpp"):
            from arbiter_agent.semif.backends.llama_cpp import LlamaCppBackend

            return LlamaCppBackend(base_url=str(config.get("semif.llama_cpp_url", "http://127.0.0.1:8088")),
                                   revision=config.get("semif.model_revision"))
    except Exception as exc:
        return NullBackend(reason=f"backend unavailable: {type(exc).__name__}: {exc}"[:200])
    return NullBackend(reason=f"backend {name!r} not available")


__all__ = ["Backend", "NullBackend", "ScoreRequest", "from_config"]
