"""Minimal-edit config writers for JSONC (JSON with comments) and block YAML (spec §4.5.2).

Re-serializing a user's settings file would drop their comments and formatting. These writers
insert or remove only Arbiter's entry as text, then parse the result and check that the only
semantic change is that entry. Anything they can't edit safely raises ``ConfigConflict``, and
setup falls back to printing a snippet.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from arbiter_agent.clients.config_merge import ConfigConflict, ConfigParseError


# ----------------------------------------------------------------------------- JSONC parsing
@dataclass
class Node:
    value: Any
    start: int                       # index of the first char of the value
    end: int                         # index just past the value
    members: dict[str, tuple[int, int, Node]] = field(default_factory=dict)   # key -> (key_start, value_end, node)


class _Parser:
    def __init__(self, text: str) -> None:
        self.s = text
        self.i = 0

    def err(self, msg: str) -> ConfigParseError:
        line = self.s.count("\n", 0, self.i) + 1
        return ConfigParseError(f"JSONC line {line}: {msg}")

    def ws(self) -> None:
        s, n = self.s, len(self.s)
        while self.i < n:
            c = s[self.i]
            if c in " \t\r\n﻿":
                self.i += 1
            elif s.startswith("//", self.i):
                j = s.find("\n", self.i)
                self.i = n if j < 0 else j + 1
            elif s.startswith("/*", self.i):
                j = s.find("*/", self.i + 2)
                if j < 0:
                    raise self.err("unterminated comment")
                self.i = j + 2
            else:
                break

    def value(self) -> Node:
        self.ws()
        if self.i >= len(self.s):
            raise self.err("unexpected end")
        c = self.s[self.i]
        start = self.i
        if c == "{":
            return self.obj()
        if c == "[":
            return self.arr()
        if c == '"':
            v = self.string()
            return Node(v, start, self.i)
        m = re.compile(r"-?\d+(\.\d+)?([eE][+-]?\d+)?|true|false|null").match(self.s, self.i)
        if not m:
            raise self.err(f"unexpected {c!r}")
        self.i = m.end()
        return Node(json.loads(m.group(0)), start, self.i)

    def string(self) -> str:
        m = re.compile(r'"(?:[^"\\]|\\.)*"', re.S).match(self.s, self.i)
        if not m:
            raise self.err("bad string")
        self.i = m.end()
        return str(json.loads(m.group(0)))

    def obj(self) -> Node:
        start = self.i
        self.i += 1
        node = Node({}, start, start)
        while True:
            self.ws()
            if self.i < len(self.s) and self.s[self.i] == "}":
                self.i += 1
                node.end = self.i
                return node
            key_start = self.i
            if self.i >= len(self.s) or self.s[self.i] != '"':
                raise self.err("expected a key")
            key = self.string()
            self.ws()
            if self.i >= len(self.s) or self.s[self.i] != ":":
                raise self.err("expected ':'")
            self.i += 1
            child = self.value()
            node.value[key] = child.value
            node.members[key] = (key_start, child.end, child)
            self.ws()
            if self.i < len(self.s) and self.s[self.i] == ",":
                self.i += 1
                continue
            self.ws()
            if self.i < len(self.s) and self.s[self.i] == "}":
                continue
            raise self.err("expected ',' or '}'")

    def arr(self) -> Node:
        start = self.i
        self.i += 1
        items: list[Any] = []
        while True:
            self.ws()
            if self.i < len(self.s) and self.s[self.i] == "]":
                self.i += 1
                return Node(items, start, self.i)
            items.append(self.value().value)
            self.ws()
            if self.i < len(self.s) and self.s[self.i] == ",":
                self.i += 1
                continue
            self.ws()
            if self.i < len(self.s) and self.s[self.i] == "]":
                continue
            raise self.err("expected ',' or ']'")


def parse_jsonc(text: str) -> Node:
    p = _Parser(text)
    node = p.value()
    p.ws()
    if p.i != len(text):
        raise p.err("trailing content")
    return node


def load_jsonc(raw: bytes | None, path: Path) -> dict[str, Any]:
    if raw is None or not raw.strip():
        return {}
    try:
        node = parse_jsonc(raw.decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise ConfigParseError(f"{path}: not UTF-8") from exc
    except ConfigParseError as exc:
        raise ConfigParseError(f"{path}: {exc}") from exc
    if not isinstance(node.value, dict):
        raise ConfigParseError(f"{path}: top level isn't an object")
    return node.value


# ----------------------------------------------------------------------------- JSONC editing
def _indent_of(text: str, pos: int) -> str:
    line_start = text.rfind("\n", 0, pos) + 1
    m = re.match(r"[ \t]*", text[line_start:])
    return m.group(0) if m else ""


def _unit(text: str) -> str:
    m = re.search(r"\n([ \t]+)\S", text)
    return m.group(1) if m else "  "


def _render(value: Any, indent: str, unit: str) -> str:
    raw = json.dumps(value, indent=len(unit) if unit.strip(" ") == "" else 1, ensure_ascii=False)
    if unit.startswith("\t"):
        raw = re.sub(r"(?m)^( +)", lambda m: "\t" * len(m.group(1)), raw)
    return raw.replace("\n", "\n" + indent)


def _dig_path(d: Any, path: list[str]) -> Any:
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def _expected_after_upsert(data: dict[str, Any], path: list[str], name: str, entry: Any) -> dict[str, Any]:
    out = copy.deepcopy(data)
    box = out
    for k in path:
        box = box.setdefault(k, {})
    box[name] = entry
    return out


def jsonc_upsert(original: bytes | None, fpath: Path, container: list[str], name: str, entry: Any,
                 is_ours: Any) -> bytes:
    text = (original or b"").decode("utf-8-sig") if original else ""
    if not text.strip():
        doc: dict[str, Any] = {}
        box = doc
        for k in container:
            box = box.setdefault(k, {})
        box[name] = entry
        return (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    root = parse_jsonc(text)
    if not isinstance(root.value, dict):
        raise ConfigConflict(f"{fpath}: top level isn't an object")
    unit = _unit(text)
    node, missing = root, list(container)
    while missing and missing[0] in node.members:
        child = node.members[missing[0]][2]
        if not isinstance(child.value, dict):
            raise ConfigConflict(f"{fpath}: '{missing[0]}' isn't an object")
        node, missing = child, missing[1:]
    if not missing and name in node.value:
        if not is_ours(node.value[name]):
            raise ConfigConflict(f"{fpath}: '{'.'.join(container)}.{name}' exists and isn't managed by Arbiter")
        if node.value[name] == entry:
            return original or b""
        key_start, value_end, _ = node.members[name]
        indent = _indent_of(text, key_start)
        new_text = text[:key_start] + json.dumps(name) + ": " + _render(entry, indent, unit) + text[value_end:]
    else:
        payload: Any = entry
        key = name
        for k in reversed(missing):
            payload, key = {key: payload}, k
        indent = _indent_of(text, node.start) + unit
        member = json.dumps(key) + ": " + _render(payload, indent, unit)
        has_members = bool(node.members)
        insert_at = node.start + 1
        new_text = text[:insert_at] + "\n" + indent + member + ("," if has_members else "\n" + _indent_of(
            text, node.start)) + text[insert_at:]
    result = new_text.encode("utf-8")
    expected = _expected_after_upsert(root.value, container, name, entry)
    if load_jsonc(result, fpath) != expected:
        raise ConfigConflict(f"{fpath}: couldn't insert the entry without changing other settings")
    return result


def jsonc_remove(current: bytes, fpath: Path, container: list[str], name: str, is_ours: Any) -> bytes:
    text = current.decode("utf-8-sig")
    root = parse_jsonc(text)
    node = root
    chain: list[tuple[Node, str]] = []
    for k in container:
        if k not in node.members:
            return current
        chain.append((node, k))
        node = node.members[k][2]
    if name not in node.members or not is_ours(node.value[name]):
        return current
    # Prune containers that would be left empty, like the plain-JSON path does.
    target_node, target_key = node, name
    while len(target_node.members) == 1 and chain:
        target_node, target_key = chain.pop()
    # Remove the member text plus one adjacent comma.
    key_start, value_end, _ = target_node.members[target_key]
    after = text[value_end:]
    m_after = re.match(r"\s*,", after)
    if m_after:
        cut_start, cut_end = key_start, value_end + m_after.end()
        # also swallow the whitespace before the key so lines don't pile up
        line_start = text.rfind("\n", 0, key_start)
        if text[line_start + 1:key_start].strip() == "":
            cut_start = line_start if line_start >= 0 else key_start
    else:
        before = text[:key_start]
        m_before = re.search(r",\s*$", before)
        cut_start = m_before.start() if m_before else key_start
        cut_end = value_end
    new_text = text[:cut_start] + text[cut_end:]
    expected = copy.deepcopy(root.value)
    box = _dig_path(expected, container)
    del box[name]
    for depth in range(len(container), 0, -1):       # mirror the pruning above
        parent = _dig_path(expected, container[:depth - 1])
        if isinstance(parent, dict) and parent.get(container[depth - 1]) == {}:
            del parent[container[depth - 1]]
        else:
            break
    result = new_text.encode("utf-8")
    if load_jsonc(result, fpath) != expected:
        raise ConfigConflict(f"{fpath}: couldn't remove the entry cleanly")
    return result


# ----------------------------------------------------------------------------- block YAML
def _yaml_load(raw: bytes | None, path: Path) -> dict[str, Any]:
    if raw is None or not raw.strip():
        return {}
    try:
        data = yaml.safe_load(raw.decode("utf-8-sig"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise ConfigParseError(f"{path}: not valid YAML ({exc})") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigParseError(f"{path}: top level isn't a mapping")
    return data


def _yaml_block(name: str, entry: dict[str, Any], indent: str, unit: str) -> str:
    body = yaml.safe_dump({name: entry}, default_flow_style=False, sort_keys=False, allow_unicode=True)
    out = []
    for line in body.splitlines():
        stripped = line.lstrip(" ")
        depth = (len(line) - len(stripped)) // 2      # safe_dump indents by 2
        out.append(indent + unit * depth + stripped + "\n")
    return "".join(out)


def yaml_upsert(original: bytes | None, fpath: Path, container: str, name: str, entry: dict[str, Any],
                is_ours: Any) -> bytes:
    """Insert ``container.name`` into a block-style YAML mapping (top-level ``container`` only)."""
    data = _yaml_load(original, fpath)
    text = (original or b"").decode("utf-8-sig") if original else ""
    box = data.get(container)
    if box is not None and not isinstance(box, dict):
        raise ConfigConflict(f"{fpath}: '{container}' isn't a mapping")
    if isinstance(box, dict) and name in box:
        if not is_ours(box[name]):
            raise ConfigConflict(f"{fpath}: '{container}.{name}' exists and isn't managed by Arbiter")
        if box[name] == entry:
            return original or b""
        text = yaml_remove(text.encode("utf-8"), fpath, container, name, is_ours).decode("utf-8")
        data = _yaml_load(text.encode("utf-8"), fpath)
        box = data.get(container)
    lines = text.splitlines(keepends=True)
    head = re.compile(rf"^{re.escape(container)}:[ \t]*(#.*)?\r?$")
    idx = next((i for i, ln in enumerate(lines) if head.match(ln.rstrip("\n"))), None)
    if idx is None:
        if box:  # present but not as a plain block header (flow style, anchors...): don't guess
            raise ConfigConflict(f"{fpath}: '{container}' isn't a plain block mapping")
        sep = "" if not text or text.endswith("\n") else "\n"
        new = text + sep + f"{container}:\n" + _yaml_block(name, entry, "  ", "  ")
    else:
        child = next((ln for ln in lines[idx + 1:] if ln.strip() and not ln.lstrip().startswith("#")), "")
        m = re.match(r"^([ \t]+)\S", child)
        indent = m.group(1) if m else "  "
        unit = indent if not indent.startswith("\t") else "\t"
        lines.insert(idx + 1, _yaml_block(name, entry, indent, unit if len(unit) <= 4 else "  "))
        new = "".join(lines)
    result = new.encode("utf-8")
    expected = copy.deepcopy(data)
    expected.setdefault(container, {})
    if expected[container] is None:
        expected[container] = {}
    expected[container][name] = entry
    if _yaml_load(result, fpath) != expected:
        raise ConfigConflict(f"{fpath}: couldn't insert the entry without changing other settings")
    return result


def yaml_remove(current: bytes, fpath: Path, container: str, name: str, is_ours: Any) -> bytes:
    data = _yaml_load(current, fpath)
    box = data.get(container)
    if not isinstance(box, dict) or name not in box or not is_ours(box[name]):
        return current
    text = current.decode("utf-8-sig")
    lines = text.splitlines(keepends=True)
    head = re.compile(rf"^{re.escape(container)}:[ \t]*(#.*)?\r?$")
    idx = next((i for i, ln in enumerate(lines) if head.match(ln.rstrip("\n"))), None)
    if idx is None:
        raise ConfigConflict(f"{fpath}: '{container}' isn't a plain block mapping")
    key = re.compile(rf"^([ \t]+){re.escape(name)}:[ \t]*(#.*)?\r?$")
    start = None
    indent = ""
    for i in range(idx + 1, len(lines)):
        ln = lines[i]
        if ln.strip() and not ln.startswith((" ", "\t")):
            break
        m = key.match(ln.rstrip("\n"))
        if m:
            start, indent = i, m.group(1)
            break
    if start is None:
        raise ConfigConflict(f"{fpath}: couldn't find '{container}.{name}' as a block entry")
    end = start + 1
    while end < len(lines):
        ln = lines[end]
        if ln.strip() and (len(ln) - len(ln.lstrip(" \t"))) <= len(indent):
            break
        end += 1
    del lines[start:end]
    if not box.keys() - {name}:
        del lines[idx]   # the container is now empty: drop its header too
    result = "".join(lines).encode("utf-8")
    expected = copy.deepcopy(data)
    del expected[container][name]
    if not expected[container]:
        del expected[container]
    if _yaml_load(result, fpath) != expected:
        raise ConfigConflict(f"{fpath}: couldn't remove the entry cleanly")
    return result
