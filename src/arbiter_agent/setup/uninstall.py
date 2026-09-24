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
from arbiter_agent.clients.claude_code import hooks as claude_hooks
from arbiter_agent.clients.codex import hooks as codex_hooks
from arbiter_agent.paths import ArbiterPaths
from arbiter_agent.setup.manifest import InstallRecord, Manifest


def _semantic_remove(rec: InstallRecord, current: bytes) -> bytes:
    path = Path(rec.path)
    if rec.kind == "toml_block":
        return cm.toml_remove_block(current, path)
    if rec.kind == "json_named_entry":
        return cm.json_transform(current, path, cm.remove_named_entry(
            str(rec.detail.get("container", "mcpServers")), str(rec.detail.get("name", "arbiter")),
            claude_hooks.mcp_is_ours))
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
        if rec.kind == "json_named_entry":  # live file: recompute if the client rewrites it meanwhile
            _, new = cm.write_with_recheck(path, lambda before: _semantic_remove(rec, before or b"{}"))
        else:
            new = _semantic_remove(rec, current)
            cm.atomic_write(path, new)
    except (cm.ConfigParseError, cm.ConfigConflict, RuntimeError, OSError) as exc:
        return f"FAILED {path}: {exc} (left unchanged)"
    return f"removed Arbiter entries from {path} (file had other changes, kept them)"


def uninstall(paths: ArbiterPaths, clients: list[str] | None = None) -> list[str]:
    manifest = Manifest.load(paths)
    out = []
    for rec in manifest.active():
        if clients and rec.client not in clients:
            continue
        result = uninstall_record(rec)
        if not result.startswith("FAILED"):
            rec.removed_at = time.time()
        out.append(result)
    manifest.save()
    return out
