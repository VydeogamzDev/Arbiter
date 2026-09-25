"""GLiNER2 classification inputs, shared by the PyTorch, ONNX and Core ML encoder backends.

GLiNER2 classifies by encoding ``( [P] head: question ( [L] a [L] b ) ) [SEP_STRUCT] ... [SEP_TEXT] text``
and scoring the embedding at each ``[L]`` marker with a small MLP (one logit per label). The
upstream processor builds the token ids and marker positions, so every backend sees exactly the
input the model was trained on; only the heavy encoder runs elsewhere.

Several heads share one pass. Arbiter uses that to score both option orders of a binary
question (its mirror, §7.4) in a single encoder call. Head names are part of the model's input,
so they're short and neutral (``q1``, ``q2``, ...).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

HEAD_PREFIX = "q"


@dataclass
class Heads:
    input_ids: Any                    # (1, seq) int64
    attention_mask: Any               # (1, seq) int64
    markers: list[list[int]]          # per head: positions of its [L] markers, in label order
    labels: list[list[str]]           # per head: the labels in the order asked


def tasks_for(heads: list[tuple[list[str], str]]) -> dict[str, dict[str, Any]]:
    """``[(labels, question), ...]`` -> a GLiNER2 classification task dict."""
    return {f"{HEAD_PREFIX}{i + 1}": {"labels": list(labels), "prompt": question}
            for i, (labels, question) in enumerate(heads)}


def build(processor: Any, text: str, heads: list[tuple[list[str], str]]) -> Heads:
    """Token ids and marker positions for ``text`` scored against ``heads`` in one pass."""
    from gliner2.inference.schema import Schema  # optional dependency: arbiter-agent[encoder]

    schema = Schema()
    for name, cfg in tasks_for(heads).items():
        schema.classification(name, cfg["labels"], prompt=cfg["prompt"])
    batch = processor.collate_fn_inference([(text, schema.build())])
    special = batch.schema_special_indices[0]
    if len(special) != len(heads):
        raise ValueError(f"expected {len(heads)} heads in the encoded schema, got {len(special)}")
    markers = [list(map(int, pos[1:])) for pos in special]          # [P] first, then one [L] per label
    for m, (labels, _) in zip(markers, heads, strict=True):
        if len(m) != len(labels):
            raise ValueError("label markers don't match the label count")
    return Heads(batch.input_ids, batch.attention_mask, markers, [list(lbl) for lbl, _ in heads])


def softmax(xs: list[float]) -> list[float]:
    import math

    top = max(xs)
    ex = [math.exp(x - top) for x in xs]
    total = sum(ex)
    return [e / total for e in ex]
