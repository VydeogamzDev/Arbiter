"""Build and apply per-client edit plans (spec §4.5.2).

A plan is a list of :class:`FileChange`. Applying one: back up the original (once), write
atomically, re-read and validate, record in the manifest. Any failure restores the backup and
leaves the other files untouched.
"""

from __future__ import annotations

import difflib
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from arbiter_agent import __version__
from arbiter_agent.clients import config_merge as cm
from arbiter_agent.clients.claude_code import hooks as claude_hooks
from arbiter_agent.clients.client_env import ClientEnv
from arbiter_agent.clients.codex import hooks as codex_hooks
from arbiter_agent.clients.profile_schema import Profile
from arbiter_agent.paths import ArbiterPaths
from arbiter_agent.setup.manifest import Manifest

Compute = Callable[[bytes | None], bytes]


@dataclass
class FileChange:
    client: str
    path: Path
    kind: str
    description: str
    compute: Compute
    live: bool = False
    mask: list[str] = field(default_factory=list)
    detail: dict[str, object] = field(default_factory=dict)
    before: bytes | None = None
    after: bytes | None = None
    error: str | None = None

    def prepare(self) -> FileChange:
        self.before = cm.read_bytes(self.path)
        try:
            self.after = self.compute(self.before)
        except (cm.ConfigConflict, cm.ConfigParseError, OSError) as exc:
            self.error = str(exc)
        return self

    @property
    def unchanged(self) -> bool:
        return self.error is None and self.after == self.before

    def diff(self) -> str:
        if self.error:
            return f"!! {self.path}: {self.error}"
        a = (self.before or b"").decode("utf-8", "replace").splitlines(keepends=True)
        b = (self.after or b"").decode("utf-8", "replace").splitlines(keepends=True)
        text = "".join(difflib.unified_diff(a, b, fromfile=str(self.path), tofile=str(self.path), n=2))
        for secret in self.mask:
            text = text.replace(secret, secret[:4] + "…(hook token)")
        return text or f"   {self.path}: already up to date\n"


def plan_for(profile: Profile, env: ClientEnv, command: list[str], *, port: int, hook_token: str) -> list[FileChange]:
    changes: list[FileChange] = []
    if profile.id == "codex":
        cfg = profile.expand(profile.mcp["file"], env)
        hooks_file = profile.expand(profile.hooks["file"], env) if profile.hooks else None
        assert cfg is not None
        entry = codex_hooks.mcp_entry(command)
        changes.append(FileChange(
            "codex", cfg, "toml_block", "register the Arbiter MCP server ([mcp_servers.arbiter])",
            lambda before: cm.toml_upsert_block(before, cfg, profile.mcp["table"], entry),
            detail={"table": profile.mcp["table"]}))
        if hooks_file is not None:
            groups = codex_hooks.hook_groups()
            changes.append(FileChange(
                "codex", hooks_file, "json_hook_groups", f"add {len(groups)} Arbiter hook groups (mcp_tool)",
                lambda before: cm.json_transform(before, hooks_file,
                                                 cm.upsert_hook_groups(groups, codex_hooks.is_ours)),
                detail={"events": sorted(groups), "transport": "mcp_tool"}))
    elif profile.id == "claude_code":
        gj = profile.expand(profile.mcp["file"], env)
        settings = profile.expand(profile.hooks["file"], env) if profile.hooks else None
        assert gj is not None
        entry = claude_hooks.mcp_entry(command)
        changes.append(FileChange(
            "claude_code", gj, "json_named_entry", "register the Arbiter MCP server (mcpServers.arbiter)",
            lambda before: cm.json_transform(before, gj, cm.upsert_named_entry(
                "mcpServers", "arbiter", entry, claude_hooks.mcp_is_ours)),
            live=True, detail={"container": "mcpServers", "name": "arbiter"}))
        if settings is not None:
            groups = claude_hooks.hook_groups(port, hook_token)
            changes.append(FileChange(
                "claude_code", settings, "json_hook_groups", f"add {len(groups)} Arbiter http hooks",
                lambda before: cm.json_transform(before, settings, cm.upsert_hook_groups(groups, claude_hooks.is_ours)),
                mask=[hook_token], detail={"events": sorted(groups), "transport": "http", "port": port}))
    return changes


def _backup(paths: ArbiterPaths, change: FileChange, stamp: str) -> str | None:
    if change.before is None:
        return None
    dest = paths.backups / "clients" / stamp / f"{change.client}-{change.path.name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(change.before)
    return str(dest)


def _validate(change: FileChange, data: bytes) -> None:
    if change.kind == "toml_block":
        if not cm.toml_block_present(data):
            raise cm.ConfigParseError(f"{change.path}: Arbiter block missing after write")
        cm._parse_toml(data.decode("utf-8"), change.path)
    else:
        cm.load_json(data, change.path)


def apply_changes(paths: ArbiterPaths, changes: list[FileChange], manifest: Manifest) -> list[str]:
    """Apply prepared changes. Returns a list of human-readable results."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out: list[str] = []
    for ch in changes:
        if ch.error:
            out.append(f"skipped {ch.path}: {ch.error}")
            continue
        existing = manifest.find(ch.client, str(ch.path))
        if ch.unchanged and existing is not None:
            out.append(f"unchanged {ch.path}")
            continue
        backup = existing.backup if existing else _backup(paths, ch, stamp)
        created = existing.created if existing else ch.before is None
        try:
            if ch.live:
                _, written = cm.write_with_recheck(ch.path, ch.compute)
            else:
                assert ch.after is not None
                cm.atomic_write(ch.path, ch.after)
                written = ch.after
            _validate(ch, cm.read_bytes(ch.path) or b"")
        except Exception as exc:
            _restore(ch, backup, created)
            out.append(f"FAILED {ch.path}: {exc} (restored original)")
            continue
        manifest.upsert(ch.client, str(ch.path), ch.kind, backup, created, cm.sha256(written) or "",
                        __version__, dict(ch.detail))
        manifest.save()
        out.append(f"updated {ch.path}")
    return out


def _restore(ch: FileChange, backup: str | None, created: bool) -> None:
    try:
        if created:
            ch.path.unlink(missing_ok=True)
        elif backup:
            shutil.copyfile(backup, ch.path)
    except OSError:
        pass
