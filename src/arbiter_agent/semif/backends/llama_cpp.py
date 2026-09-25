"""Decoder backend over a local llama.cpp ``llama-server`` (GGUF; decision 0026).

- Scoring: one forward pass with ``n_predict: 1`` and top-k *pre-sampling* token probabilities
  (post-sampling probabilities at temperature 0 are just 1.0 for the greedy token); the score of
  each option is the probability of its letter. If most of the mass isn't on option letters,
  the model didn't answer the question: abstain.
- Chat models: the prompt goes through the model's own chat template (``/apply-template``) with
  reasoning disabled (``enable_thinking: false``), so a reasoning model answers with the letter
  instead of opening a ``<think>`` block. Servers without the endpoint get the raw prompt.
- Exact budgeting: ``/tokenize`` with the deployed model's tokenizer.
- Prefix reuse: ``cache_prompt: true``. Adapters: per-request ``lora`` selection.
- The server is loopback-only; Arbiter never sends session state off the machine.
"""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from typing import Any

from arbiter_agent.concurrency import Deadline
from arbiter_agent.semif.backends.base import ScoreRequest
from arbiter_agent.semif.prompt import LETTERS
from arbiter_agent.semif.types import TEMPLATE_VERSION, ScoreResult

TOP_K = 20
MIN_LETTER_MASS = 0.5
TEMPLATE_OVERHEAD = 64       # tokens the chat template adds around the rendered prompt


class LlamaCppBackend:
    name = "llama_cpp"
    precision = "gguf"

    def __init__(self, base_url: str = "http://127.0.0.1:8088", revision: str | None = None,
                 adapters: dict[str, int] | None = None, timeout_s: float = 30.0, chat: bool = True) -> None:
        if not base_url.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]")):
            raise ValueError("llama-server must be on loopback")
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.adapters = adapters or {}
        props = self._get("/props")
        self.model = str(props.get("model_path") or props.get("model_alias") or "unknown-model").replace("\\", "/")
        self.model = self.model.rsplit("/", 1)[-1]
        self.revision = revision or f"unpinned:{props.get('build_info', 'llama.cpp')}"
        settings = props.get("default_generation_settings") or {}
        self.chat = chat and bool(props.get("chat_template"))
        self.max_tokens = int(settings.get("n_ctx") or props.get("n_ctx") or 4096) - (TEMPLATE_OVERHEAD if self.chat
                                                                                       else 0)
        self.tokenizer = f"{self.model}:tokenizer"

    # ------------------------------------------------------------------ HTTP
    def _req(self, path: str, body: dict[str, Any] | None, timeout: float) -> Any:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(  # noqa: S310 - base_url is checked to be loopback http in __init__
            self.base_url + path, data=data, method="POST" if body is not None else "GET",
            headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=max(0.05, timeout)) as r:  # noqa: S310 - loopback only (checked)
            return json.loads(r.read() or b"{}")

    def _get(self, path: str) -> Any:
        return self._req(path, None, self.timeout_s)

    # ------------------------------------------------------------------ interface
    def count_tokens(self, text: str) -> int | None:
        try:
            return len(self._req("/tokenize", {"content": text}, 5.0).get("tokens") or [])
        except (OSError, ValueError, urllib.error.URLError):
            return None

    def _templated(self, prompt: str, timeout: float) -> str:
        """The model's chat template around ``prompt``, reasoning off; raw prompt on failure."""
        if not self.chat:
            return prompt
        try:
            out = self._req("/apply-template", {"messages": [{"role": "user", "content": prompt}],
                                                "chat_template_kwargs": {"enable_thinking": False}}, timeout)
            return str(out["prompt"])
        except (OSError, ValueError, KeyError, urllib.error.URLError):
            self.chat = False                 # server without the endpoint: fall back for good
            return prompt

    @staticmethod
    def _letter_probs(resp: dict[str, Any]) -> dict[str, float]:
        out: dict[str, float] = {}
        cps = resp.get("completion_probabilities") or []
        first = cps[0] if cps else {}
        for item in first.get("probs") or []:                       # older server format
            tok = str(item.get("tok_str", "")).strip()
            out[tok] = out.get(tok, 0.0) + float(item.get("prob", 0.0))
        for item in first.get("top_logprobs") or []:                # current format, pre-sampling log-probs
            tok = str(item.get("token", "")).strip()
            out[tok] = out.get(tok, 0.0) + math.exp(float(item.get("logprob", -1e9)))
        if not first.get("top_logprobs"):
            for item in first.get("top_probs") or []:               # current format, probabilities
                tok = str(item.get("token", "")).strip()
                out[tok] = out.get(tok, 0.0) + float(item.get("prob", 0.0))
        return out

    def score(self, requests: list[ScoreRequest], deadline: Deadline) -> list[ScoreResult]:
        out: list[ScoreResult] = []
        for r in requests:
            t0 = time.perf_counter()

            def fail(reason: str, r: ScoreRequest = r, t0: float = t0) -> ScoreResult:
                return ScoreResult(list(r.options), [], self.model, self.revision, self.name, self.precision,
                                   TEMPLATE_VERSION, self.tokenizer, r.state_hash, r.criterion_hash,
                                   (time.perf_counter() - t0) * 1000, abstain=True, reason=reason)

            if deadline.expired():
                out.append(fail("deadline"))
                continue
            body: dict[str, Any] = {"prompt": self._templated(r.prompt, deadline.remaining()), "n_predict": 1,
                                    "n_probs": TOP_K, "temperature": 0.0, "cache_prompt": True}
            if r.adapter is not None and r.adapter in self.adapters:
                body["lora"] = [{"id": self.adapters[r.adapter], "scale": 1.0}]
            try:
                resp = self._req("/completion", body, deadline.remaining())
            except (OSError, ValueError, urllib.error.URLError) as exc:
                out.append(fail(f"server error: {exc}"[:200]))
                continue
            letters = self._letter_probs(resp)
            raw = [letters.get(LETTERS[i], 0.0) for i in range(len(r.options))]
            mass = sum(raw)
            if mass < MIN_LETTER_MASS:
                out.append(fail(f"model didn't answer with an option letter (mass {mass:.2f})"))
                continue
            probs = [p / mass for p in raw]
            cached = int((resp.get("timings") or {}).get("cache_n", resp.get("tokens_cached", 0)) or 0) > 0
            out.append(ScoreResult(list(r.options), probs, self.model, self.revision, self.name, self.precision,
                                   TEMPLATE_VERSION, self.tokenizer, r.state_hash, r.criterion_hash,
                                   (time.perf_counter() - t0) * 1000,
                                   cache_mode="prefix_cached" if cached else "fresh"))
        return out

    def health(self) -> dict[str, Any]:
        try:
            h = self._get("/health")
            return {"backend": self.name, "ok": h.get("status") in ("ok", "no slot available"), "model": self.model,
                    "revision": self.revision, "n_ctx": self.max_tokens}
        except (OSError, ValueError, urllib.error.URLError) as exc:
            return {"backend": self.name, "ok": False, "reason": str(exc)[:200]}

    def close(self) -> None:
        pass
