"""Export GLiNER2.5-Decide's classification path to ONNX (``arbiter semif export-onnx``).

The graph is the DeBERTa-v3 encoder plus the label classifier: ``(input_ids, attention_mask,
markers) -> logits`` with one logit per marker. Tokenization stays with the upstream processor
(see :mod:`gliner_inputs`), so the exported model sees exactly the training-time input.

Precisions (weight-only quantization keeps activations in FP32, so scores barely move):
- ``w8e4`` (default): 8-bit matmul weights and a 4-bit embedding table; about a third of the
  FP32 size.
- ``w8``: 8-bit matmul weights, FP32 embeddings (the 128k-token table is about 525 MB).
- ``fp16``: for GPU execution providers (FP16 on most CPUs is slower than FP32).
- ``fp32``: the reference export and parity baseline.

Rejected: dynamic int8 (``quantize_dynamic``) quantizes activations too and moved probe scores by
up to 0.22 with 2 of 10 top labels flipped.

Every export is checked against the PyTorch model on probe inputs before it's accepted; the
result (top-label agreement, max probability difference) is written to the manifest.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import time
from pathlib import Path
from typing import Any

MANIFEST = "arbiter-onnx.json"
FORMAT = "arbiter-gliner2-cls-onnx"
PRECISIONS = ("w8e4", "w8", "fp16", "fp32")
PARITY_MAX_DIFF = 0.05          # an export whose probe scores move more than this is refused
PROBES = [
    ("All done, the tests pass and the bug is fixed.",
     "Does this final assistant message claim that the requested work is complete?"),
    ("I'm still investigating the failing test; next I'll check the parser.",
     "Does this final assistant message claim that the requested work is complete?"),
    ("Actually, forget the cache. Can you look at the login bug instead?",
     "Does the latest user message start a new, different task?"),
    ("Also make it handle negative numbers.",
     "Does the latest user message start a new, different task?"),
    ("Keep the public API exactly the same.",
     "Is this sentence something the user wants done or kept true when the work is finished?"),
]


class ParityError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load(model_dir: Path) -> Any:
    from gliner2 import AutoExtractor

    with contextlib.redirect_stdout(io.StringIO()):
        m = AutoExtractor.from_pretrained(str(model_dir))
    m.eval()
    return m


def _probe_heads(m: Any, text: str, question: str) -> Any:
    from arbiter_agent.semif import gliner_inputs as gi

    return gi.build(m.processor, text, [(["yes", "no"], question), (["no", "yes"], question)])


def _torch_logits(m: Any, text: str, question: str) -> list[list[float]]:
    import torch

    heads = _probe_heads(m, text, question)
    with torch.no_grad():
        h = m.encoder(input_ids=heads.input_ids, attention_mask=heads.attention_mask).last_hidden_state[0]
        return [m.classifier(h[pos]).squeeze(-1).tolist() for pos in heads.markers]


def _quantize(src: Path, dst: Path, precision: str) -> None:
    import onnx

    if precision == "fp16":
        from onnxruntime.transformers.float16 import convert_float_to_float16

        onnx.save(convert_float_to_float16(onnx.load(str(src)), keep_io_types=True), str(dst))
        return
    from onnxruntime.quantization.matmul_nbits_quantizer import MatMulNBitsQuantizer

    q = MatMulNBitsQuantizer(onnx.load(str(src)), bits=8, block_size=32, is_symmetric=True, accuracy_level=4,
                             op_types_to_quantize=("MatMul",))
    q.process()
    model = q.model
    if precision == "w8e4":             # ORT's Gather quantizer is 4-bit only
        q2 = MatMulNBitsQuantizer(model.model, bits=4, block_size=32, is_symmetric=True,
                                  op_types_to_quantize=("Gather",), quant_axes=(("Gather", 1),))
        q2.process()
        model = q2.model
    model.save_model_to_file(str(dst), use_external_data_format=False)


def export(model_dir: Path, out_dir: Path, precision: str = "w8e4", *, opset: int = 17,
           log: Any = print) -> dict[str, Any]:
    import logging

    import numpy as np
    import onnxruntime as ort
    import torch

    from arbiter_agent.semif import gliner_inputs as gi
    from arbiter_agent.semif.backends.encoder import _local_revision

    if precision not in PRECISIONS:
        raise ValueError(f"precision must be one of {', '.join(PRECISIONS)}")
    logging.getLogger("onnxruntime.quantization").setLevel(logging.WARNING)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    m = _load(model_dir)
    log(f"loaded {model_dir} in {time.time() - t0:.1f}s")
    # reference scores first: tracing leaves the PyTorch model altered
    reference = {(text, q): _torch_logits(m, text, q) for text, q in PROBES}

    class Graph(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.encoder, self.classifier = m.encoder, m.classifier

        def forward(self, input_ids: Any, attention_mask: Any, markers: Any) -> Any:
            h = self.encoder(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state[0]
            return self.classifier(h.index_select(0, markers)).squeeze(-1)

    sample = gi.build(m.processor, PROBES[0][0], [(["yes", "no"], PROBES[0][1])])
    flat = torch.tensor([p for pos in sample.markers for p in pos], dtype=torch.long)
    fp32 = out_dir / "model.fp32.onnx"
    t0 = time.time()
    with contextlib.redirect_stdout(io.StringIO()):
        torch.onnx.export(Graph(), (sample.input_ids, sample.attention_mask, flat), str(fp32),
                          input_names=["input_ids", "attention_mask", "markers"], output_names=["logits"],
                          dynamic_axes={"input_ids": {1: "seq"}, "attention_mask": {1: "seq"}, "markers": {0: "k"},
                                        "logits": {0: "k"}},
                          opset_version=opset, dynamo=False, do_constant_folding=True)
    log(f"exported the FP32 graph in {time.time() - t0:.1f}s")
    final = fp32
    if precision != "fp32":
        final = out_dir / f"model.{precision}.onnx"
        _quantize(fp32, final, precision)
        fp32.unlink(missing_ok=True)
    m.processor.tokenizer.save_pretrained(str(out_dir / "tokenizer"))

    sess = ort.InferenceSession(str(final), providers=["CPUExecutionProvider"])
    max_diff, agree, n, flips = 0.0, 0, 0, 0
    for (text, q), ref in reference.items():
        heads = _probe_heads(m, text, q)
        for pos, r in zip(heads.markers, ref, strict=True):
            got = sess.run(["logits"], {"input_ids": heads.input_ids.numpy(),
                                        "attention_mask": heads.attention_mask.numpy(),
                                        "markers": np.array(pos, dtype="int64")})[0].tolist()
            pr, pg = gi.softmax(r), gi.softmax(got)
            max_diff = max(max_diff, max(abs(a - b) for a, b in zip(pr, pg, strict=True)))
            same = pr.index(max(pr)) == pg.index(max(pg))
            agree += int(same)
            flips += int(not same and max(pr) > 0.5 + PARITY_MAX_DIFF)     # a flip on a near-tie is noise
            n += 1
    parity = {"probes": n, "top_label_agreement": agree / n, "confident_flips": flips,
              "max_prob_diff": round(max_diff, 5)}
    log(f"{precision}: {final.stat().st_size / 1e6:.0f} MB, {agree}/{n} top labels agree, "
        f"max probability difference {max_diff:.4f}")
    if max_diff > PARITY_MAX_DIFF or flips:
        final.unlink(missing_ok=True)
        raise ParityError(f"{precision} export rejected: {parity}")
    manifest = {"format": FORMAT, "version": 1, "precision": precision, "file": final.name,
                "sha256": _sha256(final), "bytes": final.stat().st_size, "opset": opset,
                "source": model_dir.name, "source_revision": _local_revision(model_dir),
                "token_pooling": m.processor.token_pooling, "max_tokens": 512, "parity": parity,
                "created_at": time.time()}
    (out_dir / MANIFEST).write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest
