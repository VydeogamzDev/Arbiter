"""Tier 0 encoder on Apple Core ML (macOS): Fluid Inference's GLiNER2.5-Decide packages.

Source: ``FluidInference/gliner2-5-decide-coreml`` (Apache-2.0), a fixed-shape export of the
classification path. Their published parity on Fastino's dev split: fp16 changes 0 of 2,900
answers, w8 changes 7; about 15 ms per call at 256 tokens on an M5 Pro.

Package contract (from the model card): inputs ``input_ids`` int32 ``[1, L]``, ``attention_mask``
int32 ``[1, L]``, ``marker_indices`` int32 ``[1, 4, K]``, ``marker_mask`` float32 ``[1, 4, K]``;
outputs ``logits`` and ``probabilities`` float32 ``[1, 4, K]`` (per-head softmax). Buckets are
``L128_H4_K8``, ``L128_H4_K32``, ``L256_H4_K32`` and ``L512_H4_K32``; the smallest bucket that fits
the request is used. Compute units must be ``ALL`` (``CPU_AND_NE`` is ~50x slower for this graph).

Up to four heads share one call, so both option orders of a binary question cost one pass.
Requires macOS 14+ and ``coremltools``; download with ``local_dir=`` (Core ML rejects the Hub
cache's symlinked weights).
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from arbiter_agent.concurrency import Deadline
from arbiter_agent.semif import gliner_inputs as gi
from arbiter_agent.semif.backends.base import ScoreRequest
from arbiter_agent.semif.backends.encoder_onnx import group_by_state
from arbiter_agent.semif.types import TEMPLATE_VERSION, ScoreResult

PACKAGE = re.compile(r"gliner2_decide_classification_(?P<prec>\w+?)_L(?P<L>\d+)_H(?P<H>\d+)_K(?P<K>\d+)\.mlpackage$")


def buckets(folder: Path, precision: str) -> list[tuple[int, int, int, Path]]:
    """``(L, H, K, path)`` for the packages of one precision, smallest first."""
    out = []
    for p in folder.iterdir():
        m = PACKAGE.match(p.name)
        if m and m["prec"] == precision:
            out.append((int(m["L"]), int(m["H"]), int(m["K"]), p))
    return sorted(out, key=lambda b: (b[0], b[2]))


class CoreMLEncoderBackend:
    name = "encoder_coreml"

    def __init__(self, folder: str | Path, precision: str = "w8", *, processor: Any = None,
                 loader: Any = None) -> None:
        self.folder = Path(folder).expanduser()
        self.precision = precision
        self._buckets = buckets(self.folder, precision)
        if not self._buckets:
            raise FileNotFoundError(f"no {precision} Core ML packages in {self.folder}")
        self.max_tokens = max(b[0] for b in self._buckets)
        self.model = "GLiNER2.5-Decide"
        self.revision = f"coreml:{precision}:{self.folder.name}"
        if processor is None:
            from gliner2.models.base import load_extractor_tokenizer
            from gliner2.processor import SchemaTransformer

            pooling = "first"      # the packages were exported with first-subword pooling
            processor = SchemaTransformer(tokenizer=load_extractor_tokenizer(str(self.folder)), token_pooling=pooling)
        self._processor = processor
        self.tokenizer = type(getattr(processor, "tokenizer", None)).__name__
        self._loader = loader or self._load_package
        self._models: dict[Path, Any] = {}

    @staticmethod
    def _load_package(path: Path) -> Any:
        import coremltools as ct  # macOS only

        return ct.models.MLModel(str(path), compute_units=ct.ComputeUnit.ALL)

    def _model_for(self, path: Path) -> Any:
        if path not in self._models:
            self._models[path] = self._loader(path)
        return self._models[path]

    def count_tokens(self, text: str) -> int | None:
        tok = getattr(self._processor, "tokenizer", None)
        try:
            return len(tok.encode(text)) if tok is not None else None
        except Exception:
            return None

    def _pick(self, seq: int, heads: int, labels: int) -> tuple[int, int, int, Path]:
        for b in self._buckets:
            if seq <= b[0] and heads <= b[1] and labels <= b[2]:
                return b
        raise ValueError(f"request needs {seq} tokens, {heads} heads, {labels} labels: no bucket fits")

    def _run(self, h: gi.Heads) -> list[list[float]]:
        import numpy as np

        ids = np.asarray(h.input_ids)
        seq = ids.shape[1]
        L, H, K, path = self._pick(seq, len(h.markers), max(len(m) for m in h.markers))
        pad = int(getattr(getattr(self._processor, "tokenizer", None), "pad_token_id", 0) or 0)
        idx = np.zeros((1, H, K), dtype=np.int32)
        mask = np.zeros((1, H, K), dtype=np.float32)
        for j, pos in enumerate(h.markers):
            idx[0, j, : len(pos)] = pos
            mask[0, j, : len(pos)] = 1.0
        feed = {"input_ids": np.pad(ids, ((0, 0), (0, L - seq)), constant_values=pad).astype(np.int32),
                "attention_mask": np.pad(np.asarray(h.attention_mask), ((0, 0), (0, L - seq))).astype(np.int32),
                "marker_indices": idx, "marker_mask": mask}
        out = self._model_for(path).predict(feed)
        logits = np.asarray(out["logits"])[0]
        # renormalize over the used slots from the logits (the package softmax includes -1e4 padding)
        return [gi.softmax([float(x) for x in logits[j, : len(pos)]]) for j, pos in enumerate(h.markers)]

    def score(self, requests: list[ScoreRequest], deadline: Deadline) -> list[ScoreResult]:
        results: list[ScoreResult | None] = [None] * len(requests)
        for state, idxs in group_by_state(requests).items():
            t0 = time.perf_counter()
            reqs = [requests[i] for i in idxs]
            probs_per: list[list[float] | None] = [None] * len(reqs)
            reason = "deadline"
            if not deadline.expired():
                try:
                    probs_per = []
                    for k in range(0, len(reqs), 4):                  # a package scores up to 4 heads
                        chunk = reqs[k:k + 4]
                        heads = gi.build(self._processor, state, [(r.options, r.criterion) for r in chunk])
                        probs_per.extend(self._run(heads))
                    reason = ""
                except Exception as exc:
                    probs_per, reason = [None] * len(reqs), f"encoder error: {exc}"[:200]
            ms = (time.perf_counter() - t0) * 1000 / max(1, len(reqs))
            for i, r, probs in zip(idxs, reqs, probs_per, strict=True):
                results[i] = ScoreResult(list(r.options), probs or [], self.model, self.revision, self.name,
                                         self.precision, TEMPLATE_VERSION, self.tokenizer, r.state_hash,
                                         r.criterion_hash, ms, abstain=probs is None, reason=reason)
        return [r for r in results if r is not None]

    def health(self) -> dict[str, Any]:
        return {"backend": self.name, "ok": True, "model": self.model, "revision": self.revision,
                "buckets": [f"L{b[0]}_H{b[1]}_K{b[2]}" for b in self._buckets]}

    def close(self) -> None:
        self._models.clear()
