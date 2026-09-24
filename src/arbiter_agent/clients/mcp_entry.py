"""Profile-driven MCP server entries (spec §4.4.2, M5).

A profile describes *where* its client keeps MCP servers (file per platform, container path,
format) and *what* an entry looks like (a template). This module renders the entry and edits
the file in that format: plain JSON (re-serialized), JSONC and block YAML (minimal text edits),
or TOML (Arbiter-managed block).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from arbiter_agent.clients import config_merge as cm
from arbiter_agent.clients import text_config as tc

DEFAULT_TEMPLATE: dict[str, Any] = {"command": "{command}", "args": "{args}"}
ENTRY_FORMATS = {"json_named_entry", "jsonc_named_entry", "yaml_named_entry"}


def render(template: Any, command: list[str]) -> Any:
    """Fill ``{command}`` (executable), ``{args}`` (arguments) and ``{argv}`` (both) placeholders."""
    if isinstance(template, dict):
        return {k: render(v, command) for k, v in template.items()}
    if isinstance(template, list):
        return [render(v, command) for v in template]
    if template == "{command}":
        return command[0]
    if template == "{args}":
        return list(command[1:])
    if template == "{argv}":
        return list(command)
    return template


def is_ours(entry: Any) -> bool:
    """An MCP entry that runs ``arbiter ... mcp``, whatever the client's field names."""
    if not isinstance(entry, dict):
        return False
    strings: list[str] = []

    def walk(v: Any) -> None:
        if isinstance(v, str):
            strings.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk({k: v for k, v in entry.items() if k not in ("env", "description", "name", "type")})
    return any("arbiter" in s.lower() for s in strings) and "mcp" in strings


def container_path(spec: dict[str, Any]) -> list[str]:
    c = spec.get("container", "mcpServers")
    return [str(x) for x in c] if isinstance(c, list) else [str(c)]


def _json_upsert(path_keys: list[str], name: str, entry: Any) -> cm.JsonMutator:
    def mutate(d: dict[str, Any]) -> dict[str, Any]:
        box: Any = d
        for k in path_keys:
            nxt = box.get(k)
            if nxt is None:
                nxt = box[k] = {}
            if not isinstance(nxt, dict):
                raise cm.ConfigConflict(f"'{k}' isn't an object")
            box = nxt
        if name in box and not is_ours(box[name]):
            raise cm.ConfigConflict(f"'{'.'.join(path_keys)}.{name}' exists and isn't managed by Arbiter")
        box[name] = entry
        return d

    return mutate


def _json_remove(path_keys: list[str], name: str) -> cm.JsonMutator:
    def mutate(d: dict[str, Any]) -> dict[str, Any]:
        chain: list[tuple[dict[str, Any], str]] = []
        box: Any = d
        for k in path_keys:
            if not isinstance(box, dict) or not isinstance(box.get(k), dict):
                return d
            chain.append((box, k))
            box = box[k]
        if name in box and is_ours(box[name]):
            del box[name]
            for parent, k in reversed(chain):   # prune containers left empty
                if parent[k] == {}:
                    del parent[k]
                else:
                    break
        return d

    return mutate


def upsert(fmt: str, original: bytes | None, path: Path, spec: dict[str, Any], entry: Any) -> bytes:
    keys, name = container_path(spec), str(spec.get("name", "arbiter"))
    if fmt == "json_named_entry":
        return cm.json_transform(original, path, _json_upsert(keys, name, entry))
    if fmt == "jsonc_named_entry":
        return tc.jsonc_upsert(original, path, keys, name, entry, is_ours)
    if fmt == "yaml_named_entry":
        if len(keys) != 1:
            raise cm.ConfigConflict("YAML entries support a top-level container only")
        return tc.yaml_upsert(original, path, keys[0], name, entry, is_ours)
    raise ValueError(f"unsupported entry format {fmt}")


def remove(fmt: str, current: bytes, path: Path, detail: dict[str, Any]) -> bytes:
    keys = [str(x) for x in detail.get("container_path") or [detail.get("container", "mcpServers")]]
    name = str(detail.get("name", "arbiter"))
    if fmt == "json_named_entry":
        return cm.json_transform(current, path, _json_remove(keys, name))
    if fmt == "jsonc_named_entry":
        return tc.jsonc_remove(current, path, keys, name, is_ours)
    if fmt == "yaml_named_entry":
        return tc.yaml_remove(current, path, keys[0], name, is_ours)
    raise ValueError(f"unsupported entry format {fmt}")


def validate(fmt: str, data: bytes, path: Path) -> None:
    if fmt == "json_named_entry":
        cm.load_json(data, path)
    elif fmt == "jsonc_named_entry":
        tc.load_jsonc(data, path)
    elif fmt == "yaml_named_entry":
        tc._yaml_load(data, path)


def snippet(fmt: str, spec: dict[str, Any], entry: Any) -> str:
    """Text a user can paste when setup can't edit the file."""
    keys, name = container_path(spec), str(spec.get("name", "arbiter"))
    doc: Any = {name: entry}
    for k in reversed(keys):
        doc = {k: doc}
    if fmt == "yaml_named_entry":
        import yaml

        return str(yaml.safe_dump(doc, default_flow_style=False, sort_keys=False))
    return json.dumps(doc, indent=2) + "\n"
