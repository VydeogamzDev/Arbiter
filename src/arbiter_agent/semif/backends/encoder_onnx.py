"""Tier 0 encoder on ONNX Runtime: GLiNER2.5-Decide exported by ``arbiter semif export-onnx``.

Same scores as the PyTorch backend (parity is checked at export time), at a fraction of the
memory: the int8 export is about a quarter of the FP32 weights. Tokenization uses the upstream
processor built from the saved tokenizer; no model weights are loaded into PyTorch.

Both option orders of a binary question are scored as two heads in one encoder pass.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from arbiter_agent.concurrency import Deadline
from arbiter_agent.semif import gliner_inputs as gi
from arbiter_agent.semif.backends.base import ScoreRequest
from arbiter_agent.semif.types import TEMPLATE_VERSION, ScoreResult

ENCODER_MAX_TOKENS = 512


def load_manifest(folder: Path) -> dict[str, Any]:
    from arbiter_agent.semif.onnx_export import FORMAT, MANIFEST

    path = folder / MANIFEST
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("format") != FORMAT:
        raise ValueError(f"{path} isn't an Arbiter GLiNER2 ONNX export")
    return data


def group_by_state(requests: list[ScoreRequest]) -> dict[str, list[int]]:
    groups: dict[str, list[int]] = {}
    for i, r in enumerate(requests):
        groups.setdefault(r.state_text, []).append(i)
    return groups


class OnnxEncoderBackend:
    name = "encoder_onnx"
    max_tokens = ENCODER_MAX_TOKENS

    def __init__(self, folder: str | Path, *, providers: list[str] | None = None, threads: int = 0,
                 session: Any = None, processor: Any = None) -> None:
        self.folder = Path(folder).expanduser()
        self.manifest = load_manifest(self.folder)
        self.precision = str(self.manifest["precision"])
        self.model = str(self.manifest.get("source") or self.folder.name)
        self.revision = f"onnx:{self.precision}:{str(self.manifest['sha256'])[:12]}"
        if processor is None:
            from gliner2.processor import SchemaTransformer
            from transformers import AutoTokenizer

            processor = SchemaTransformer(tokenizer=AutoTokenizer.from_pretrained(str(self.folder / "tokenizer")),
                                          token_pooling=str(self.manifest.get("token_pooling", "first")))
        self._processor = processor
        self.tokenizer = type(getattr(processor, "tokenizer", None)).__name__
        if session is None:
            import onnxruntime as ort

            opts = ort.SessionOptions()
            if threads:
                opts.intra_op_num_threads = threads
            session = ort.InferenceSession(str(self.folder / self.manifest["file"]), opts,
                                           providers=providers or ["CPUExecutionProvider"])
        self._session = session

    def count_tokens(self, text: str) -> int | None:
        tok = getattr(self._processor, "tokenizer", None)
        try:
            return len(tok.encode(text)) if tok is not None else None
        except Exception:
            return None

    def _run(self, heads: gi.Heads) -> list[list[float]]:
        import numpy as np

        flat = np.array([p for pos in heads.markers for p in pos], dtype="int64")
        logits = self._session.run(["logits"], {"input_ids": np.asarray(heads.input_ids, dtype="int64"),
                                                "attention_mask": np.asarray(heads.attention_mask, dtype="int64"),
                                                "markers": flat})[0].tolist()
        out, i = [], 0
        for pos in heads.markers:
            out.append(gi.softmax([float(x) for x in logits[i:i + len(pos)]]))
            i += len(pos)
        return out

    def score(self, requests: list[ScoreRequest], deadline: Deadline) -> list[ScoreResult]:
        results: list[ScoreResult | None] = [None] * len(requests)
        for state, idxs in group_by_state(requests).items():
            t0 = time.perf_counter()
            reqs = [requests[i] for i in idxs]
            if deadline.expired():
                probs_per: list[list[float] | None] = [None] * len(reqs)
                reason = "deadline"
            else:
                try:
                    heads = gi.build(self._processor, state, [(r.options, r.criterion) for r in reqs])
                    probs_per = list(self._run(heads))
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
                "precision": self.precision, "parity": self.manifest.get("parity")}

    def close(self) -> None:
        pass
