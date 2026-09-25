"""Semantic tool ranking with the tier-0 encoder (spec §10.4, §10.5).

One encoder pass scores every tool in the catalog as a label of a single classification head
("Which tool does this task need?"). The ranking only adds candidates to the lexical results; it
can't hide a tool, grant anything, or change what a tool may do (the sensor ranks, never
authorizes). Measured on the gateway corpus (2026-09-25, 73 tools, CPU ONNX w8e4): hybrid recall@8
0.95 on held-out tasks vs 0.85 for lexical alone, about 2 s per search on CPU.
"""

from __future__ import annotations

from typing import Any

from arbiter_agent.gateway.catalog import Tool

QUESTION = "Which tool does this task need?"
MAX_LABELS = 120            # label markers must fit the encoder's 512-token window with the task text


class EncoderRanker:
    def __init__(self, backend: Any) -> None:
        self.backend = backend          # OnnxEncoderBackend (or anything with _processor and _run)

    def rank(self, query: str, tools: list[Tool]) -> list[str]:
        from arbiter_agent.semif import gliner_inputs as gi

        tools = tools[:MAX_LABELS]
        labels = [f"{t.server} {t.name.replace('_', ' ')}" for t in tools]
        heads = gi.build(self.backend._processor, query, [(labels, QUESTION)])
        probs = self.backend._run(heads)[0]
        return [tools[i].tool_id for i in sorted(range(len(tools)), key=lambda i: -probs[i])]


def ranker_from_config(config: Any) -> EncoderRanker | None:
    """The encoder ranker when ``gateway.semantic_search`` allows it and an ONNX tier-0 model is
    configured; otherwise None (lexical only)."""
    mode = str(config.get("gateway.semantic_search", "auto")) if config is not None else "off"
    if mode == "off":
        return None
    enc = config.get("semif.encoder") or {}
    try:
        from arbiter_agent.semif.backends import encoder_runtime

        model = str(enc.get("model") or "")
        if encoder_runtime(model, str(enc.get("runtime", "auto"))) != "onnx":
            return None
        from arbiter_agent.semif.backends.encoder_onnx import OnnxEncoderBackend

        return EncoderRanker(OnnxEncoderBackend(model))
    except Exception:
        return None
