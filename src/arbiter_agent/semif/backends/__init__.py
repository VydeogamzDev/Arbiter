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
            return encoder_backend(enc)
        name = str(config.get("semif.backend", "auto"))
        if name in ("auto", "llama_cpp"):
            from arbiter_agent.semif.backends.llama_cpp import LlamaCppBackend

            return LlamaCppBackend(base_url=str(config.get("semif.llama_cpp_url", "http://127.0.0.1:8088")),
                                   revision=config.get("semif.model_revision"))
    except Exception as exc:
        return NullBackend(reason=f"backend unavailable: {type(exc).__name__}: {exc}"[:200])
    return NullBackend(reason=f"backend {name!r} not available")


def encoder_runtime(model: str, runtime: str = "auto") -> str:
    """Which tier 0 runtime serves ``model``: an Arbiter ONNX export -> onnx; Core ML packages on
    macOS -> coreml; anything else (weights folder, Hub id) -> torch."""
    if runtime != "auto":
        return runtime
    import sys
    from pathlib import Path

    folder = Path(model).expanduser()
    if folder.is_dir():
        if (folder / "arbiter-onnx.json").exists():
            return "onnx"
        if sys.platform == "darwin" and any(folder.glob("*.mlpackage")):
            return "coreml"
    return "torch"


def encoder_backend(enc: dict[str, Any]) -> Backend:
    model = str(enc.get("model", "fastino/GLiNER2.5-Decide"))
    runtime = encoder_runtime(model, str(enc.get("runtime", "auto")))
    if runtime == "onnx":
        from arbiter_agent.semif.backends.encoder_onnx import OnnxEncoderBackend

        return OnnxEncoderBackend(model)
    if runtime == "coreml":
        from arbiter_agent.semif.backends.encoder_coreml import CoreMLEncoderBackend

        return CoreMLEncoderBackend(model, precision=str(enc.get("precision", "w8")))
    from arbiter_agent.semif.backends.encoder import EncoderBackend

    return EncoderBackend(model_id=model, device=str(enc.get("device", "auto")), revision=enc.get("revision"))


__all__ = ["Backend", "NullBackend", "ScoreRequest", "encoder_backend", "encoder_runtime", "from_config"]
