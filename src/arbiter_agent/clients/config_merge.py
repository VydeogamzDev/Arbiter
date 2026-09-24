"""Safe, reversible edits to client config files (spec §4.5.2).

Rules:
- Parse before and after every edit; if the original can't be parsed, don't touch the file.
- TOML: Arbiter's entry is a marker-delimited block appended to the file, so removal restores
  the user's bytes exactly and never reorders their content.
- JSON: Arbiter's entries are merged semantically; formatting (indent, trailing newline) follows
  the original. Entries are identified by content, never by position.
- Writes are atomic (temp file + replace). For files a client may rewrite concurrently
  (``~/.claude.json``), the merge is recomputed if the file changes between read and write.
- Arbiter never edits entries it didn't create; a same-named user entry is a conflict.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BLOCK_START = "# >>> arbiter: managed by `arbiter setup` (remove with `arbiter uninstall`)"
BLOCK_END = "# <<< arbiter"
NL = "\n"
_BLOCK_RE = re.compile(r"\n?" + re.escape(BLOCK_START) + r"\n.*?" + re.escape(BLOCK_END) + r"\n?", re.S)


class ConfigConflict(RuntimeError):
    """A user-owned entry occupies the name Arbiter wants to use."""


class ConfigParseError(RuntimeError):
    """The existing file isn't valid; Arbiter refuses to edit it."""


def sha256(data: bytes | None) -> str | None:
    return hashlib.sha256(data).hexdigest() if data is not None else None


def read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def atomic_write(path: Path, data: bytes, retries: int = 10) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.arbiter-{os.getpid()}.tmp")
    tmp.write_bytes(data)
    for attempt in range(retries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # Windows: target briefly open by another process
            if attempt == retries - 1:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05 * (attempt + 1))


# ----------------------------------------------------------------------------- TOML
def toml_str(s: str) -> str:
    if "'" not in s and "\n" not in s:
        return f"'{s}'"
    return json.dumps(s)  # a JSON string is a valid TOML basic string


def toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return toml_str(v)
    if isinstance(v, list):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k} = {toml_value(x)}" for k, x in v.items()) + " }"
    raise TypeError(f"unsupported TOML value {type(v).__name__}")


def render_toml_table(table: str, values: dict[str, Any]) -> str:
    lines = [BLOCK_START, f"[{table}]"]
    lines += [f"{k} = {toml_value(v)}" for k, v in values.items()]
    lines.append(BLOCK_END)
    return "\n".join(lines) + "\n"


def _parse_toml(text: str, path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigParseError(f"{path}: not valid TOML ({exc})") from exc


def _dig(d: dict[str, Any], dotted: str) -> Any:
    cur: Any = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _without(d: dict[str, Any], dotted: str) -> dict[str, Any]:
    out = copy.deepcopy(d)
    parts = dotted.split(".")
    cur = out
    for p in parts[:-1]:
        if not isinstance(cur.get(p), dict):
            return out
        cur = cur[p]
    cur.pop(parts[-1], None)
    # prune parent tables left empty by the removal ([mcp_servers] with only arbiter in it)
    for depth in range(len(parts) - 1, 0, -1):
        node: Any = out
        for p in parts[: depth - 1]:
            node = node.get(p, {}) if isinstance(node, dict) else {}
        key = parts[depth - 1]
        if isinstance(node, dict) and node.get(key) == {}:
            del node[key]
    return out


def toml_upsert_block(original: bytes | None, path: Path, table: str, values: dict[str, Any]) -> bytes:
    """Return new file bytes with Arbiter's block present (replacing an older Arbiter block).
    Re-running with the same values yields identical bytes."""
    text = (original or b"").decode("utf-8")
    managed = BLOCK_START in text
    if text.strip():
        _parse_toml(text, path)  # refuse to edit a file that's already invalid
    stripped = _BLOCK_RE.sub(NL, text) if managed else text
    base = _parse_toml(stripped, path) if stripped.strip() else {}
    if not managed and _dig(base, table) is not None:
        raise ConfigConflict(f"{path}: [{table}] already exists and isn't managed by Arbiter")
    top = table.split(".")[0]
    if top in base and not isinstance(base[top], dict):
        raise ConfigConflict(f"{path}: '{top}' isn't a table")
    body = stripped.rstrip(NL)
    new_text = (body + NL + NL if body else "") + render_toml_table(table, values)
    after = _parse_toml(new_text, path)
    if _dig(after, table) != values:
        raise ConfigParseError(f"{path}: merged [{table}] did not round-trip")
    if _without(after, table) != _without(base, table):
        raise ConfigParseError(f"{path}: merge would alter other settings")
    return new_text.encode("utf-8")


def toml_remove_block(current: bytes, path: Path) -> bytes:
    text = current.decode("utf-8")
    if BLOCK_START not in text:
        return current
    m = _BLOCK_RE.search(text)
    assert m is not None
    new_text = text[: m.start()] + text[m.end():]
    if m.start() > 0 and not new_text.endswith("\n") and new_text:
        new_text += "\n"
    if new_text.strip():
        _parse_toml(new_text, path)
    return new_text.encode("utf-8")


def toml_block_present(current: bytes | None) -> bool:
    return bool(current) and BLOCK_START.encode() in (current or b"")


# ----------------------------------------------------------------------------- JSON
@dataclass(frozen=True)
class JsonStyle:
    indent: int | None
    trailing_newline: bool

    @staticmethod
    def detect(raw: bytes | None) -> JsonStyle:
        if not raw:
            return JsonStyle(2, True)
        text = raw.decode("utf-8-sig")
        m = re.search(r"\n( +|\t+)\S", text)
        indent: int | None = len(m.group(1)) if m and m.group(1).startswith(" ") else (2 if m else None)
        return JsonStyle(indent, text.endswith("\n"))


def load_json(raw: bytes | None, path: Path) -> dict[str, Any]:
    if raw is None or not raw.strip():
        return {}
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ConfigParseError(f"{path}: not valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise ConfigParseError(f"{path}: top level isn't an object")
    return data


def dump_json(data: dict[str, Any], style: JsonStyle) -> bytes:
    text = json.dumps(data, indent=style.indent, ensure_ascii=False)
    return (text + ("\n" if style.trailing_newline else "")).encode("utf-8")


JsonMutator = Callable[[dict[str, Any]], dict[str, Any]]


def json_transform(original: bytes | None, path: Path, mutate: JsonMutator) -> bytes:
    data = load_json(original, path)
    new = mutate(copy.deepcopy(data))
    out = dump_json(new, JsonStyle.detect(original))
    load_json(out, path)  # round-trip check
    return out


def write_with_recheck(path: Path, compute: Callable[[bytes | None], bytes],
                       attempts: int = 3) -> tuple[bytes | None, bytes]:
    """Compute new bytes from the current file and write them, recomputing if the file changes
    underneath (a client rewriting its own config). Returns (bytes before, bytes written)."""
    for _ in range(attempts):
        before = read_bytes(path)
        new = compute(before)
        if read_bytes(path) != before:
            continue
        atomic_write(path, new)
        return before, new
    raise RuntimeError(f"{path} kept changing while Arbiter tried to update it; try again")


# JSON helpers for the two structures Arbiter edits ---------------------------------
def upsert_named_entry(container_key: str, name: str, entry: dict[str, Any], is_ours: Callable[[Any], bool]
                       ) -> JsonMutator:
    def mutate(d: dict[str, Any]) -> dict[str, Any]:
        box = d.get(container_key)
        if box is None:
            box = d[container_key] = {}
        if not isinstance(box, dict):
            raise ConfigConflict(f"'{container_key}' isn't an object")
        if name in box and not is_ours(box[name]):
            raise ConfigConflict(f"'{container_key}.{name}' exists and isn't managed by Arbiter")
        box[name] = entry
        return d

    return mutate


def remove_named_entry(container_key: str, name: str, is_ours: Callable[[Any], bool]) -> JsonMutator:
    def mutate(d: dict[str, Any]) -> dict[str, Any]:
        box = d.get(container_key)
        if isinstance(box, dict) and name in box and is_ours(box[name]):
            del box[name]
            if not box:
                del d[container_key]
        return d

    return mutate


def upsert_hook_groups(groups: dict[str, dict[str, Any]], is_ours: Callable[[dict[str, Any]], bool]) -> JsonMutator:
    """``groups``: event name -> one matcher group ``{"matcher"?: ..., "hooks": [handler]}``.
    Arbiter's group for an event is replaced in place (keeping its index), else appended."""
    def mutate(d: dict[str, Any]) -> dict[str, Any]:
        hooks = d.get("hooks")
        if hooks is None:
            hooks = d["hooks"] = {}
        if not isinstance(hooks, dict):
            raise ConfigConflict("'hooks' isn't an object")
        for event, group in groups.items():
            lst = hooks.get(event)
            if lst is None:
                lst = hooks[event] = []
            if not isinstance(lst, list):
                raise ConfigConflict(f"'hooks.{event}' isn't a list")
            for i, existing in enumerate(lst):
                if isinstance(existing, dict) and _group_is_ours(existing, is_ours):
                    lst[i] = group
                    break
            else:
                lst.append(group)
        return d

    return mutate


def remove_hook_groups(is_ours: Callable[[dict[str, Any]], bool]) -> JsonMutator:
    def mutate(d: dict[str, Any]) -> dict[str, Any]:
        hooks = d.get("hooks")
        if not isinstance(hooks, dict):
            return d
        for event in list(hooks):
            lst = hooks[event]
            if not isinstance(lst, list):
                continue
            kept = [g for g in lst if not (isinstance(g, dict) and _group_is_ours(g, is_ours))]
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]
        if not hooks:
            del d["hooks"]
        return d

    return mutate


def _group_is_ours(group: dict[str, Any], is_ours: Callable[[dict[str, Any]], bool]) -> bool:
    handlers = group.get("hooks")
    return isinstance(handlers, list) and bool(handlers) and all(isinstance(h, dict) and is_ours(h) for h in handlers)


def count_hook_groups(raw: bytes | None, path: Path, is_ours: Callable[[dict[str, Any]], bool]) -> int:
    try:
        d = load_json(raw, path)
    except ConfigParseError:
        return 0
    hooks = d.get("hooks")
    if not isinstance(hooks, dict):
        return 0
    return sum(1 for lst in hooks.values() if isinstance(lst, list)
               for g in lst if isinstance(g, dict) and _group_is_ours(g, is_ours))
