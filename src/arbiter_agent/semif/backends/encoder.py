"""Tier 0 encoder backend: GLiNER2.5-Decide (340M, CPU or GPU; decision 0026).

Optional dependency: ``gliner2[local]`` (``arbiter-agent[encoder]``); the bare package only calls
Fastino's hosted API, which Arbiter never uses. ``model_id`` may be a local folder. The model classifies a
passage against labelled options with an optional question (``classify_text``). When the library
reports a confidence it becomes a score; a bare label is marked ``hard_label`` so the mirroring
step treats it as uninformative until calibrated.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from arbiter_agent.concurrency import Deadline
from arbiter_agent.semif.backends.base import ScoreRequest
from arbiter_agent.semif.types import TEMPLATE_VERSION, ScoreResult

ENCODER_MAX_TOKENS = 512     # DeBERTa-v3 was trained on 512-token inputs


def _local_revision(folder: Path) -> str | None:
    """A cheap identity for a local model folder: config hash plus weights size (hashing 2 GB of
    weights on every start would cost seconds). A changed checkpoint changes the revision."""
    cfg, weights = folder / "config.json", folder / "model.safetensors"
    if not (folder.is_dir() and cfg.exists() and weights.exists()):
        return None
    return f"local:{hashlib.sha256(cfg.read_bytes()).hexdigest()[:12]}:{weights.stat().st_size}"


class EncoderBackend:
    name = "encoder"
    precision = "fp32"
    max_tokens = ENCODER_MAX_TOKENS

    def __init__(self, model_id: str = "fastino/GLiNER2.5-Decide", device: str = "auto",
                 revision: str | None = None, model: Any = None) -> None:
        self.model_id = model_id
        self.device = device
        local = Path(model_id).expanduser()
        if model is None:
            import contextlib
            import io

            from gliner2 import AutoExtractor  # optional dependency: arbiter-agent[encoder]

            # gliner2 prints a banner (with emoji) while loading; on a cp1252 console or a detached
            # daemon that crashes the load, so it's captured.
            with contextlib.redirect_stdout(io.StringIO()):
                model = AutoExtractor.from_pretrained(str(local) if local.is_dir() else model_id,
                                                      **({"revision": revision} if revision else {}))
            if device not in ("auto", "cpu") and hasattr(model, "to"):
                model = model.to(device)
        self._model = model
        self.model = local.name if local.is_dir() else model_id
        self.revision = revision or _local_revision(local) or f"unpinned:{getattr(model, 'version', 'hub')}"
        tok = getattr(model, "tokenizer", None) or getattr(getattr(model, "processor", None), "tokenizer", None)
        self._tokenizer = tok if callable(getattr(tok, "encode", None)) else None
        self.tokenizer = type(tok).__name__ if tok is not None else "bytes-upper-bound"

    def count_tokens(self, text: str) -> int | None:
        if self._tokenizer is None:
            return None
        try:
            return len(self._tokenizer.encode(text))
        except Exception:
            return None

    def _classify(self, text: str, options: list[str], criterion: str) -> tuple[list[float], bool]:
        schema = {"answer": {"labels": list(options), "prompt": criterion}}
        try:
            out = self._model.classify_text(text, schema, include_confidence=True)
        except TypeError:
            out = self._model.classify_text(text, schema)
        val = out.get("answer") if isinstance(out, dict) else None
        if isinstance(val, dict) and "label" in val:
            label, conf = str(val["label"]), float(val.get("confidence", 1.0))
            if label not in options or not 0 <= conf <= 1:
                raise ValueError(f"unexpected encoder output {val!r}")
            rest = (1.0 - conf) / max(1, len(options) - 1)
            return [conf if o == label else rest for o in options], False
        if isinstance(val, str) and val in options:
            return [1.0 if o == val else 0.0 for o in options], True
        raise ValueError(f"unexpected encoder output {out!r}")

    def score(self, requests: list[ScoreRequest], deadline: Deadline) -> list[ScoreResult]:
        out: list[ScoreResult] = []
        for r in requests:
            t0 = time.perf_counter()
            if deadline.expired():
                out.append(ScoreResult(r.options, [], self.model, self.revision, self.name, self.precision,
                                       TEMPLATE_VERSION, self.tokenizer, r.state_hash, r.criterion_hash, 0.0,
                                       abstain=True, reason="deadline"))
                continue
            try:
                probs, hard = self._classify(r.state_text, r.options, r.criterion)
                out.append(ScoreResult(list(r.options), probs, self.model, self.revision, self.name, self.precision,
                                       TEMPLATE_VERSION, self.tokenizer, r.state_hash, r.criterion_hash,
                                       (time.perf_counter() - t0) * 1000, hard_label=hard))
            except Exception as exc:
                out.append(ScoreResult(list(r.options), [], self.model, self.revision, self.name, self.precision,
                                       TEMPLATE_VERSION, self.tokenizer, r.state_hash, r.criterion_hash,
                                       (time.perf_counter() - t0) * 1000, abstain=True,
                                       reason=f"encoder error: {exc}"[:200]))
        return out

    def health(self) -> dict[str, Any]:
        return {"backend": self.name, "ok": True, "model": self.model, "revision": self.revision,
                "device": self.device}

    def close(self) -> None:
        self._model = None
