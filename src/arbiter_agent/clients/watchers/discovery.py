"""Transcript discovery: find recently active session files of installed clients and attach
tailers (decision 0020). Scans are cheap (recent date folders / recent mtimes) and throttled."""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from arbiter_agent.clients.client_env import ClientEnv

if TYPE_CHECKING:
    from arbiter_agent.clients.ingest import Ingestor
    from arbiter_agent.clients.watchers.transcript_tail import WatcherManager
    from arbiter_agent.state.writer import Writer

RECENT_S = 24 * 3600
SCAN_INTERVAL_S = 5.0


class Discovery:
    def __init__(self, env: ClientEnv, clients: list[str], ingestor: Ingestor, writer: Writer,
                 recent_s: float = RECENT_S, interval_s: float = SCAN_INTERVAL_S) -> None:
        self.env = env
        self.clients = clients
        self.ingestor = ingestor
        self.writer = writer
        self.recent_s = recent_s
        self.interval_s = interval_s
        self._last = 0.0
        self.found: dict[str, int] = {}

    def _codex_files(self, now: float) -> list[Path]:
        root = self.env.codex_sessions
        days = {datetime.fromtimestamp(now) - timedelta(days=d) for d in (0, 1)}
        out: list[Path] = []
        for day in days:
            folder = root / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"
            if folder.is_dir():
                out += [p for p in folder.glob("rollout-*.jsonl") if now - p.stat().st_mtime < self.recent_s]
        return out

    def _claude_files(self, now: float) -> list[Path]:
        root = self.env.claude_projects
        if not root.is_dir():
            return []
        out = []
        for proj in root.iterdir():
            if not proj.is_dir():
                continue
            try:
                for p in proj.glob("*.jsonl"):
                    if now - p.stat().st_mtime < self.recent_s:
                        out.append(p)
            except OSError:
                continue
        return out

    def scan(self, manager: WatcherManager, force: bool = False) -> int:
        from arbiter_agent.clients.watchers.transcript_tail import TranscriptTailer

        now = time.time()
        if not force and now - self._last < self.interval_s:
            return 0
        self._last = now
        added = 0
        for client in self.clients:
            if client == "codex":
                files, parser = self._codex_files(now), "codex_rollout_v1"
            elif client == "claude_code":
                files, parser = self._claude_files(now), "claude_code_v1"
            else:
                continue
            for f in files:
                sid = f"{client}:{str(f.resolve()).replace(os.sep, '/').lower()}"
                if manager.has(sid):
                    continue
                manager.add(TranscriptTailer(client=client, path=f, parser=parser, ingestor=self.ingestor,
                                             writer=self.writer))
                added += 1
                self.found[client] = self.found.get(client, 0) + 1
        return added
