"""Adopted-server registry: which servers the gateway proxies for which client, how to launch them,
and the user's read-only confirmations. Written with user-only permissions (entries can hold
credentials in their env, just like the client's own config)."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from arbiter_agent.paths import ArbiterPaths, write_private


@dataclass
class Adopted:
    client: str
    server: str
    entry: dict[str, Any]                       # the client's original config entry (launch spec source)
    confirmed_read_only: dict[str, str] = field(default_factory=dict)   # tool name -> confirmed schema hash
    mutating: list[str] = field(default_factory=list)
    adopted_at: float = field(default_factory=time.time)


class Registry:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.items: list[Adopted] = []

    @classmethod
    def load(cls, paths: ArbiterPaths) -> Registry:
        r = cls(paths.config / "gateway" / "adopted.json")
        try:
            data = json.loads(r.path.read_text(encoding="utf-8"))
            r.items = [Adopted(**x) for x in data.get("adopted", [])]
        except (OSError, ValueError, TypeError):
            r.items = []
        return r

    def save(self) -> None:
        write_private(self.path, json.dumps({"version": 1, "adopted": [asdict(a) for a in self.items]},
                                            indent=1).encode())

    def for_client(self, client: str) -> list[Adopted]:
        return [a for a in self.items if a.client == client]

    def get(self, client: str, server: str) -> Adopted | None:
        return next((a for a in self.items if a.client == client and a.server == server), None)

    def put(self, a: Adopted) -> None:
        self.items = [x for x in self.items if not (x.client == a.client and x.server == a.server)] + [a]

    def drop(self, client: str, server: str) -> None:
        self.items = [x for x in self.items if not (x.client == client and x.server == server)]
