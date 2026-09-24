"""The sensor service (spec §7.1, §7.8).

A question goes: route to a stage (tier 0 encoder or decoder) -> fit the budget (§7.2) ->
build variants (mirrors, paraphrase: §7.4) -> batch variants that share a prefix -> score under
a deadline -> validate every result (§7.3) -> aggregate or abstain.

Reliability: bounded queue with shedding, deadlines on every call, per-family circuit breakers,
backend back-off after repeated errors. Nothing here is ever awaited on a hook path: callers
use :meth:`submit` from background work, or :meth:`judge` from the CLI and benchmark.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any

from arbiter_agent.completion.breakers import Breaker
from arbiter_agent.concurrency import Deadline, Priority, WorkQueue
from arbiter_agent.semif import mirroring, prompt
from arbiter_agent.semif.backends import Backend, NullBackend, ScoreRequest
from arbiter_agent.semif.request_budget import BudgetError, fit
from arbiter_agent.semif.types import Judgment, Question, ScoreResult
from arbiter_agent.semif.validation import validate

log = logging.getLogger("arbiter.semif")
BACKOFF_S = (5.0, 30.0, 120.0, 600.0)


class SemIfService:
    def __init__(self, decoder: Backend | None = None, encoder: Backend | None = None, *,
                 encoder_families: list[str] | None = None, adapters: dict[str, str] | None = None,
                 timeout_s: float = 1.2, queue_capacity: int = 32, expected_revision: str | None = None,
                 board: Any = None) -> None:
        self.decoder: Backend = decoder or NullBackend()
        self.encoder: Backend = encoder or NullBackend(reason="tier 0 encoder disabled")
        self.encoder_families = set(encoder_families or [])
        self.adapters = adapters or {}
        self.timeout_s = timeout_s
        self.expected_revision = expected_revision
        self.queue = WorkQueue("semif", capacity=queue_capacity)
        self.breakers: dict[str, Breaker] = {}
        self.board = board              # policy BreakerBoard: family breakers become visible and resettable
        self._lock = threading.Lock()
        self._errors: dict[str, int] = {}
        self._backoff_until: dict[str, float] = {}
        self.stats = {"judged": 0, "abstained": 0, "invalid": 0, "shed": 0, "budget_rejected": 0}

    @classmethod
    def from_config(cls, config: Any, board: Any = None) -> SemIfService:
        from arbiter_agent.semif.backends import from_config

        enc = config.get("semif.encoder") or {}
        return cls(decoder=from_config(config, "decoder"), encoder=from_config(config, "encoder"),
                   encoder_families=list(enc.get("families") or []), adapters=dict(config.get("semif.adapters") or {}),
                   timeout_s=float(config.get("semif.timeout_ms", 1200)) / 1000.0,
                   queue_capacity=int(config.get("semif.queue_capacity", 32)),
                   expected_revision=config.get("semif.model_revision"), board=board)

    def start(self) -> SemIfService:
        self.queue.start()
        return self

    def stop(self) -> None:
        self.queue.stop()
        for b in (self.decoder, self.encoder):
            try:
                b.close()
            except Exception:
                pass

    # ------------------------------------------------------------------ routing + health
    def backend_for(self, family: str) -> Backend:
        if family in self.encoder_families and not isinstance(self.encoder, NullBackend):
            return self.encoder
        return self.decoder

    def _breaker(self, family: str) -> Breaker:
        with self._lock:
            b = self.breakers.get(family)
            if b is None:
                b = self.breakers[family] = (self.board.get("semif", family) if self.board is not None else
                                             Breaker(f"semif:{family}", threshold=5, cooldown_s=300))
            return b

    def _backend_error(self, backend: Backend) -> None:
        with self._lock:
            n = self._errors[backend.name] = self._errors.get(backend.name, 0) + 1
            self._backoff_until[backend.name] = time.monotonic() + BACKOFF_S[min(n, len(BACKOFF_S)) - 1]

    def _backend_ok(self, backend: Backend) -> None:
        with self._lock:
            self._errors.pop(backend.name, None)
            self._backoff_until.pop(backend.name, None)

    def _backing_off(self, backend: Backend) -> bool:
        return time.monotonic() < self._backoff_until.get(backend.name, 0.0)

    # ------------------------------------------------------------------ judging
    def judge(self, q: Question, deadline_s: float | None = None) -> Judgment:
        deadline = Deadline(self.timeout_s if deadline_s is None else deadline_s)
        backend = self.backend_for(q.family)
        breaker = self._breaker(q.family)

        def abstain(reason: str) -> Judgment:
            self.stats["abstained"] += 1
            return Judgment(q.family, None, {}, 0.0, 0.0, 0.0, 0, True, reason)

        if isinstance(backend, NullBackend):
            return abstain(f"no semantic score available ({backend.reason})")
        if breaker.is_open():
            return abstain(f"circuit breaker open for {q.family}")
        if self._backing_off(backend):
            return abstain(f"{backend.name} backing off after errors")
        try:
            fitted = fit(q, backend.max_tokens, backend.count_tokens)
        except BudgetError as exc:
            self.stats["budget_rejected"] += 1
            return abstain(f"budget: {exc}")
        adapter = self.adapters.get(q.family)
        variant_list = mirroring.variants(q)
        reqs = [ScoreRequest(q.family, opts, crit or q.criterion, fitted.state_text,
                             prompt.render(q, fitted.state_text, opts, crit), adapter) for opts, crit in variant_list]
        try:
            results = backend.score(reqs, deadline)
        except Exception as exc:
            self._backend_error(backend)
            breaker.record_failure(f"{type(exc).__name__}: {exc}")
            return abstain(f"backend error: {exc}"[:200])
        scored: list[tuple[list[str], ScoreResult]] = []
        for req, res in zip(reqs, results, strict=True):
            ok, why = validate(res, q, req.options, req.state_hash, expected_revision=self.expected_revision)
            if not ok and not res.abstain:
                self.stats["invalid"] += 1
                breaker.record_failure(why)
                res.abstain, res.reason = True, f"invalid result: {why}"
            scored.append((req.options, res))
        if any(not r.abstain for _, r in scored):
            self._backend_ok(backend)
            breaker.record_success()
        j = mirroring.aggregate(q, scored)
        self.stats["judged"] += 1
        if j.abstain:
            self.stats["abstained"] += 1
        return j

    def submit(self, q: Question, callback: Callable[[Judgment], None] | None = None,
               priority: Priority = Priority.BACKGROUND, deadline_s: float | None = None) -> Future[Judgment] | None:
        """Queue a question for background scoring. Returns None when shed (queue full)."""
        fut: Future[Judgment] = Future()

        def job() -> None:
            try:
                j = self.judge(q, deadline_s)
            except Exception as exc:
                j = Judgment(q.family, None, {}, 0.0, 0.0, 0.0, 0, True, f"error: {exc}"[:200])
            fut.set_result(j)
            if callback is not None:
                try:
                    callback(j)
                except Exception:
                    log.exception("sensor callback failed")

        if not self.queue.submit(job, priority):
            self.stats["shed"] += 1
            return None
        return fut

    def health(self) -> dict[str, Any]:
        return {"decoder": self.decoder.health(), "encoder": self.encoder.health(),
                "encoder_families": sorted(self.encoder_families), "stats": dict(self.stats),
                "queue": {"backlog": self.queue.backlog, **self.queue.stats},
                "breakers": {k: b.to_dict() for k, b in self.breakers.items()}}
