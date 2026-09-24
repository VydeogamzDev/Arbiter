"""Work priorities: lower value runs first. Gating work never waits behind background work."""

from __future__ import annotations

from enum import IntEnum


class Priority(IntEnum):
    GATING = 0        # on a hook's critical path (rarely queued; usually run inline)
    STATE = 10        # intent/epoch/fact updates that later gates depend on
    TELEMETRY = 20    # diff stats, integrity refresh, loop detection
    BACKGROUND = 30   # baseline capture, retention-like sweeps
