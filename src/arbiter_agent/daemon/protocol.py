"""Shim <-> daemon message protocol (spec §16.5). Stdlib only.

Frames are single JSON objects (never pickle). The first request on every connection is
``hello``; the reply says whether the peer is compatible and what to do if not:

- same major version                    -> ``serve``
- client major newer than daemon major  -> ``drain_restart`` (daemon drains and exits; the next
                                           call launches the newer daemon; the client passes through)
- client major older than daemon major  -> ``passthrough`` (the stale shim fails open;
                                           doctor reports it)
"""

from __future__ import annotations

import json
from typing import Any

from arbiter_agent import PROTOCOL_MAJOR, PROTOCOL_MINOR, __version__

MAX_FRAME = 8 * 1024 * 1024


class ProtocolError(RuntimeError):
    pass


def encode(obj: dict[str, Any]) -> bytes:
    data = json.dumps(obj, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
    if len(data) > MAX_FRAME:
        raise ProtocolError("frame too large")
    return data


def decode(data: bytes) -> dict[str, Any]:
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProtocolError(f"bad frame: {exc}") from exc
    if not isinstance(obj, dict):
        raise ProtocolError("frame must be an object")
    return obj


def hello_params(component: str) -> dict[str, Any]:
    return {"protocol": [PROTOCOL_MAJOR, PROTOCOL_MINOR], "version": __version__, "component": component}


def negotiate(client_protocol: Any) -> dict[str, Any]:
    """Server-side decision for a client's ``[major, minor]``."""
    try:
        major, minor = int(client_protocol[0]), int(client_protocol[1])
    except (TypeError, ValueError, IndexError):
        return {"compatible": False, "action": "passthrough", "reason": "malformed protocol version"}
    base = {"protocol": [PROTOCOL_MAJOR, PROTOCOL_MINOR], "version": __version__}
    if major == PROTOCOL_MAJOR:
        return {**base, "compatible": True, "action": "serve", "client_minor": minor}
    if major > PROTOCOL_MAJOR:
        return {**base, "compatible": False, "action": "drain_restart"}
    return {**base, "compatible": False, "action": "passthrough"}
