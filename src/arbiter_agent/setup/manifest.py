"""Install manifest (spec §4.5.2): every file Arbiter edits, with the original backup and the
hash of what Arbiter wrote. Uninstall uses it to restore byte-identical files when nothing else
changed, or to remove only Arbiter's entries when the user (or the client) edited the file since.

Kept as a JSON file (not in SQLite) so setup and uninstall work whether or not the daemon runs.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from arbiter_agent.paths import ArbiterPaths, write_private

MANIFEST_VERSION = 1


@dataclass
class InstallRecord:
    client: str
    path: str
    kind: str                       # toml_block | json_named_entry | json_hook_groups
    backup: str | None              # copy of the file *before the first* Arbiter edit (None = file didn't exist)
    created: bool                   # Arbiter created the file
    written_sha256: str             # hash of the bytes Arbiter last wrote
    installed_at: float
    updated_at: float
    arbiter_version: str
    detail: dict[str, Any] = field(default_factory=dict)
    removed_at: float | None = None


@dataclass
class Manifest:
    path: Path
    records: list[InstallRecord] = field(default_factory=list)

    @classmethod
    def load(cls, paths: ArbiterPaths) -> Manifest:
        p = paths.state / "install_manifest.json"
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            recs = [InstallRecord(**r) for r in data.get("records", [])]
        except (OSError, ValueError, TypeError):
            recs = []
        return cls(p, recs)

    def save(self) -> None:
        data = {"version": MANIFEST_VERSION, "records": [asdict(r) for r in self.records]}
        write_private(self.path, json.dumps(data, indent=1).encode())

    def active(self, client: str | None = None) -> list[InstallRecord]:
        return [r for r in self.records if r.removed_at is None and (client is None or r.client == client)]

    def find(self, client: str, path: str, kind: str | None = None) -> InstallRecord | None:
        for r in self.active(client):
            if r.path == path and (kind is None or r.kind == kind):
                return r
        return None

    def for_path(self, path: str) -> list[InstallRecord]:
        """Active records for one file, oldest first (several kinds can share a file)."""
        return sorted((r for r in self.active() if r.path == path), key=lambda r: r.installed_at)

    def installed_clients(self) -> list[str]:
        return sorted({r.client for r in self.active()})

    def upsert(self, client: str, path: str, kind: str, backup: str | None, created: bool, written_sha256: str,
               version: str, detail: dict[str, Any]) -> InstallRecord:
        now = time.time()
        rec = self.find(client, path, kind)
        if rec is None:
            rec = InstallRecord(client, path, kind, backup, created, written_sha256, now, now, version, detail)
            self.records.append(rec)
        else:  # keep the *original* backup so uninstall can restore pre-Arbiter bytes
            rec.written_sha256, rec.updated_at, rec.arbiter_version, rec.detail = written_sha256, now, version, detail
        for other in self.for_path(path):   # every record for the file knows Arbiter's latest bytes
            other.written_sha256 = written_sha256
        return rec
