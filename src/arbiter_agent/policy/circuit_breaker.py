"""Circuit breakers (spec §15.3, §18.6; decision 0009).

A breaker trips after ``threshold`` failures inside ``window_s``. While open, the component it
guards takes its §18.6 fail mode and, when the breaker names a feature flag, that flag is forced
off. After ``cooldown_s`` it goes half-open: work is allowed again, one failure reopens it, and it
closes only after ``recovery_successes`` consecutive clean outcomes (the recovery window).
``latched`` breakers (incidents such as a false completion) never cool down: only a manual
reset after inspection closes them.

The board owns every breaker, keyed by kind plus an optional key (a client, a runner, a sensor
family). Trips, closes and resets are reported to ``on_event`` (the daemon audits them) and
latched/open state is persisted so a restart can't silently clear an incident.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from arbiter_agent.policy.fail_modes import MATRIX

EventFn = Callable[[str, "Breaker", str], None]      # (event: trip|close|reset, breaker, reason)


@dataclass
class Breaker:
    name: str
    threshold: int = 3                 # failures within window to trip
    window_s: float = 600.0
    cooldown_s: float = 600.0
    recovery_successes: int = 1        # consecutive clean outcomes needed to close from half-open
    latched: bool = False              # only a manual reset closes it
    failures: deque[float] = field(default_factory=deque)
    opened_at: float | None = None
    trips: int = 0
    last_reason: str = ""
    _clean: int = 0
    listener: EventFn | None = None

    def _half_open(self, now: float) -> bool:
        return self.opened_at is not None and not self.latched and now - self.opened_at >= self.cooldown_s

    def _trip(self, now: float) -> None:
        self.opened_at = now
        self.trips += 1
        self._clean = 0
        if self.listener:
            self.listener("trip", self, self.last_reason)

    def record_failure(self, reason: str = "", now: float | None = None) -> None:
        now = now or time.time()
        self.failures.append(now)
        self.last_reason = reason[:200]
        while self.failures and self.failures[0] < now - self.window_s:
            self.failures.popleft()
        if self._half_open(now):
            self._trip(now)                       # a failed trial reopens immediately
        elif self.opened_at is None and len(self.failures) >= self.threshold:
            self._trip(now)

    def record_success(self, now: float | None = None) -> None:
        now = now or time.time()
        if self._half_open(now):
            self._clean += 1
            if self._clean >= self.recovery_successes:
                self._close("recovered after a clean window")

    def _close(self, reason: str) -> None:
        self.opened_at = None
        self.failures.clear()
        self._clean = 0
        if self.listener:
            self.listener("close", self, reason)

    def reset(self, reason: str = "manual reset") -> bool:
        was_open = self.opened_at is not None
        if was_open:
            self._close(reason)
        self.failures.clear()
        return was_open

    def is_open(self, now: float | None = None) -> bool:
        now = now or time.time()
        if self.opened_at is None:
            return False
        return not self._half_open(now)

    def state(self, now: float | None = None) -> str:
        now = now or time.time()
        if self.opened_at is None:
            return "closed"
        return "half_open" if self._half_open(now) else "open"

    def to_dict(self) -> dict[str, Any]:
        return {"open": self.is_open(), "state": self.state(), "trips": self.trips,
                "recent_failures": len(self.failures), "last_reason": self.last_reason,
                "latched": self.latched}


class LatencyBreaker(Breaker):
    def __init__(self, name: str, budget_ms: float, samples: int = 50, min_samples: int = 20,
                 cooldown_s: float = 600.0) -> None:
        super().__init__(name, threshold=1, cooldown_s=cooldown_s)
        self.budget_ms = budget_ms
        self.lat: deque[float] = deque(maxlen=samples)
        self.min_samples = min_samples

    def record_latency(self, ms: float, now: float | None = None) -> None:
        self.lat.append(ms)
        if len(self.lat) >= self.min_samples and self.p95() > self.budget_ms:
            if self.opened_at is None or self._half_open(now or time.time()):
                self.record_failure(f"p95 {self.p95():.0f} ms > {self.budget_ms:.0f} ms", now)
        else:
            self.record_success(now)

    def p95(self) -> float:
        if not self.lat:
            return 0.0
        s = sorted(self.lat)
        return s[min(len(s) - 1, round(0.95 * (len(s) - 1)))]

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), "p95_ms": round(self.p95(), 1), "samples": len(self.lat)}


@dataclass(frozen=True)
class BreakerSpec:
    kind: str
    component: str                     # key into the §18.6 fail-mode matrix
    description: str
    flags: tuple[str, ...] = ()        # feature flags forced off while open (restored for half-open trials)
    threshold: int = 3
    window_s: float = 600.0
    cooldown_s: float = 600.0
    recovery_successes: int = 3
    latched: bool = False
    integrity: bool = False            # resetting it weakens integrity: needs an interactive user


SPECS: dict[str, BreakerSpec] = {s.kind: s for s in [
    BreakerSpec("hook_latency", "completion_gate", "gating-hook p95 latency above budget", threshold=1,
                recovery_successes=1),
    BreakerSpec("gate_errors", "completion_gate", "exceptions while evaluating the gate", recovery_successes=1,
                integrity=True),
    BreakerSpec("parser", "verification_parsers", "a runner's parser failing or contradicted by the exit status",
                recovery_successes=1, integrity=True),
    BreakerSpec("false_complete", "completion_gate",
                "a verified completion contradicted by a later failure with no code change", threshold=1,
                latched=True, integrity=True),
    BreakerSpec("client", "client_adapters", "a client's hooks failing in the daemon (dialect or engine errors)",
                threshold=5, window_s=300, cooldown_s=300),
    BreakerSpec("schema_miss", "client_profile", "repeated payloads the client profile doesn't recognize",
                threshold=20, window_s=600),
    BreakerSpec("controller", "controller", "controller timeouts or error spike", threshold=10, window_s=300,
                cooldown_s=300, flags=("status_injection", "semif")),
    BreakerSpec("retrieval", "retrieval", "index refresh or query errors", threshold=5, window_s=600,
                flags=("repo_index",)),
    BreakerSpec("semif", "semif", "sensor results invalid or backend failing, per decision family", threshold=5,
                cooldown_s=300, recovery_successes=3),
    BreakerSpec("semif_parity", "semif", "quantized model disagrees with the reference backend beyond tolerance",
                threshold=1, latched=True, flags=("semif",)),
    BreakerSpec("stale_result", "speculation", "a stale speculation result was consumed", threshold=1,
                latched=True, flags=("speculation",), integrity=True),
    BreakerSpec("context_restore", "context_compaction", "context restore failed", threshold=1, latched=True,
                flags=("context_scheduler",), integrity=True),
    BreakerSpec("learned_policy", "learned_policy", "learned policy realized loss outside its bound", threshold=1,
                latched=True),
]}
for _s in SPECS.values():
    assert _s.component in MATRIX, _s.component


class BreakerBoard:
    def __init__(self, flags: Any = None, on_event: EventFn | None = None, enabled: bool = True,
                 overrides: dict[str, dict[str, Any]] | None = None) -> None:
        self.flags = flags
        self.on_event = on_event
        self.enabled = enabled
        self._overrides = overrides or {}          # kind -> spec field overrides (config / tests)
        self._lock = threading.RLock()
        self._breakers: dict[str, Breaker] = {}

    @staticmethod
    def name_of(kind: str, key: str | None = None) -> str:
        return f"{kind}:{key}" if key else kind

    def spec_of(self, name: str) -> BreakerSpec:
        return SPECS[name.split(":", 1)[0]]

    def add(self, b: Breaker, kind: str) -> Breaker:
        """Register an existing breaker instance (e.g. the latency breaker) under a spec."""
        with self._lock:
            b.listener = self._event
            self._breakers[b.name] = b
            SPECS[kind]  # validate
            return b

    def get(self, kind: str, key: str | None = None) -> Breaker:
        name = self.name_of(kind, key)
        with self._lock:
            b = self._breakers.get(name)
            if b is None:
                s = SPECS[kind]
                o = self._overrides.get(kind, {})
                b = Breaker(name, threshold=int(o.get("threshold", s.threshold)),
                            window_s=float(o.get("window_s", s.window_s)),
                            cooldown_s=float(o.get("cooldown_s", s.cooldown_s)),
                            recovery_successes=int(o.get("recovery_successes", s.recovery_successes)),
                            latched=s.latched, listener=self._event)
                self._breakers[name] = b
            return b

    def failure(self, kind: str, reason: str = "", key: str | None = None) -> None:
        if self.enabled:
            self.get(kind, key).record_failure(reason)

    def success(self, kind: str, key: str | None = None) -> None:
        name = self.name_of(kind, key)
        b = self._breakers.get(name)
        if b is not None:
            b.record_success()

    def is_open(self, kind: str, key: str | None = None) -> bool:
        if not self.enabled:
            return False
        b = self._breakers.get(self.name_of(kind, key))
        return bool(b and b.is_open())

    def reset(self, name: str, reason: str = "manual reset") -> bool:
        with self._lock:
            b = self._breakers.get(name)
        if b is None:
            raise KeyError(f"no breaker named {name!r}")
        was = b.reset(reason)
        if self.on_event:
            self.on_event("reset", b, reason)
        return was

    def open_names(self) -> list[str]:
        return sorted(n for n, b in self._breakers.items() if b.is_open())

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            items = list(self._breakers.items())
        out = {}
        for n, b in sorted(items):
            s = self.spec_of(n)
            out[n] = {**b.to_dict(), "component": s.component, "fail_mode": MATRIX[s.component].mode.value,
                      "flags": list(s.flags), "integrity": s.integrity}
        return out

    def _flag_held(self, flag: str, exclude: Breaker | None = None) -> bool:
        """Is some other breaker that guards ``flag`` still open (not yet half-open)?"""
        return any(o is not exclude and o.is_open() and flag in self.spec_of(n).flags
                   for n, o in list(self._breakers.items()))

    def _event(self, event: str, b: Breaker, reason: str) -> None:
        s = self.spec_of(b.name)
        if self.flags is not None:
            for flag in s.flags:
                if event == "trip":
                    self.flags.force_off(flag, f"breaker {b.name}: {reason}"[:200])
                elif not self._flag_held(flag, b):
                    self.flags.clear_force(flag)
        if self.on_event:
            try:
                self.on_event(event, b, reason)
            except Exception:
                pass

    def tick(self) -> None:
        """Let half-open breakers' modules run trial work: lift their forced-off flags once the
        cooldown has passed (a failed trial trips the breaker, and the flags, again)."""
        if self.flags is None:
            return
        for n, b in list(self._breakers.items()):
            if b.state() == "half_open":
                for flag in self.spec_of(n).flags:
                    if not self._flag_held(flag, b):
                        self.flags.clear_force(flag)

    def guard(self, kind: str, fn: Callable[[], Any], *, flag: str | None = None, key: str | None = None,
              passthrough: tuple[type[BaseException], ...] = (LookupError, ValueError),
              unavailable: str = "unavailable") -> Any:
        """Run ``fn`` behind breaker ``kind``: refuse while its flag is forced off (the caller's
        fail mode applies), count errors other than ``passthrough`` (caller mistakes), and count
        clean runs toward recovery."""
        self.tick()
        if flag is not None and self.flags is not None and not self.flags.enabled(flag):
            raise RuntimeError(unavailable)
        try:
            out = fn()
        except passthrough:
            raise
        except Exception as exc:
            self.failure(kind, f"{type(exc).__name__}: {exc}", key=key)
            raise
        self.success(kind, key=key)
        return out

    # ------------------------------------------------------------------ persistence
    def persisted(self) -> list[dict[str, Any]]:
        """Open and latched state worth surviving a restart."""
        return [{"name": n, "opened_at": b.opened_at, "trips": b.trips, "last_reason": b.last_reason}
                for n, b in list(self._breakers.items()) if b.opened_at is not None]

    def restore(self, rows: list[dict[str, Any]]) -> None:
        for r in rows:
            name = str(r["name"])
            kind, _, key = name.partition(":")
            if kind not in SPECS:
                continue
            b = self.get(kind, key or None)
            b.opened_at, b.trips, b.last_reason = float(r["opened_at"]), int(r["trips"]), str(r["last_reason"])
            if self.flags is not None and b.is_open():
                for flag in SPECS[kind].flags:
                    self.flags.force_off(flag, f"breaker {name}: {b.last_reason} (restored)"[:200])
