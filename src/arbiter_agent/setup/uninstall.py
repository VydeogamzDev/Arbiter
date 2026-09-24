"""``arbiter uninstall`` (spec §4.5.4).

For each manifest record:
- file unchanged since Arbiter wrote it -> restore the pre-Arbiter backup byte-for-byte
  (or delete the file if Arbiter created it);
- file changed since (user or client edits) -> remove only Arbiter's entries, keep the rest.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from arbiter_agent.clients import config_merge as cm
from arbiter_agent.clients import mcp_entry
from arbiter_agent.clients.claude_code import hooks as claude_hooks
from arbiter_agent.clients.codex import hooks as codex_hooks
from arbiter_agent.paths import ArbiterPaths
from arbiter_agent.setup.manifest import InstallRecord, Manifest


def _semantic_remove(rec: InstallRecord, current: bytes) -> bytes:
    path = Path(rec.path)
    if rec.kind == "toml_block":
        return cm.toml_remove_block(current, path)
    if rec.kind == "json_named_entry" and "container_path" not in rec.detail:   # Claude Code (M2 records)
        return cm.json_transform(current, path, cm.remove_named_entry(
            str(rec.detail.get("container", "mcpServers")), str(rec.detail.get("name", "arbiter")),
            claude_hooks.mcp_is_ours))
    if rec.kind in mcp_entry.ENTRY_FORMATS:
        return mcp_entry.remove(rec.kind, current, path, dict(rec.detail))
    from arbiter_agent.clients.hook_dialects import DIALECTS

    for d in DIALECTS.values():
        if rec.kind == d.hook_format:
            return d.remove(current, path)
    if rec.kind == "json_hook_groups":
        is_ours = codex_hooks.is_ours if rec.client == "codex" else claude_hooks.is_ours
        return cm.json_transform(current, path, cm.remove_hook_groups(is_ours))
    raise ValueError(f"unknown record kind {rec.kind}")


def uninstall_record(rec: InstallRecord) -> str:
    path = Path(rec.path)
    current = cm.read_bytes(path)
    if current is None:
        return f"gone already {path}"
    if cm.sha256(current) == rec.written_sha256:
        if rec.created:
            path.unlink()
            return f"deleted {path} (Arbiter created it)"
        if rec.backup and Path(rec.backup).exists():
            shutil.copyfile(rec.backup, path)
            return f"restored {path} (byte-identical to before setup)"
    try:
        if rec.kind in mcp_entry.ENTRY_FORMATS:  # recompute if the client rewrites the file meanwhile
            _, new = cm.write_with_recheck(path, lambda before: _semantic_remove(rec, before or b"{}"))
        else:
            new = _semantic_remove(rec, current)
            cm.atomic_write(path, new)
    except (cm.ConfigParseError, cm.ConfigConflict, RuntimeError, OSError) as exc:
        return f"FAILED {path}: {exc} (left unchanged)"
    return f"removed Arbiter entries from {path} (file had other changes, kept them)"


def uninstall_file(recs: list[InstallRecord]) -> str:
    """Undo every Arbiter record for one file (oldest first). If the file is exactly what Arbiter
    last wrote, restore the pre-Arbiter bytes; otherwise remove each record's entries."""
    if len(recs) == 1:
        return uninstall_record(recs[0])
    path = Path(recs[0].path)
    current = cm.read_bytes(path)
    if current is None:
        return f"gone already {path}"
    first = recs[0]
    if cm.sha256(current) == recs[-1].written_sha256:
        if first.created:
            path.unlink()
            return f"deleted {path} (Arbiter created it)"
        if first.backup and Path(first.backup).exists():
            shutil.copyfile(first.backup, path)
            return f"restored {path} (byte-identical to before setup)"
    try:
        new = current
        for rec in reversed(recs):
            new = _semantic_remove(rec, new)
        cm.atomic_write(path, new)
    except (cm.ConfigParseError, cm.ConfigConflict, RuntimeError, OSError, ValueError) as exc:
        return f"FAILED {path}: {exc} (left unchanged)"
    return f"removed Arbiter entries from {path} (file had other changes, kept them)"


def uninstall(paths: ArbiterPaths, clients: list[str] | None = None) -> list[str]:
    manifest = Manifest.load(paths)
    out = []
    by_path: dict[str, list[InstallRecord]] = {}
    for rec in manifest.active():
        if clients and rec.client not in clients:
            continue
        by_path.setdefault(rec.path, []).append(rec)
    for recs in by_path.values():
        recs.sort(key=lambda r: r.installed_at)
        result = uninstall_file(recs)
        if not result.startswith("FAILED"):
            for rec in recs:
                rec.removed_at = time.time()
        out.append(result)
    manifest.save()
    return out
