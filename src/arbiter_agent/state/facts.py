"""OBSERVED FACTS with evidence origin (spec §6.4.1), extracted from normalized events.

- Shell tool results become ``test_run`` facts when a runner parser recognizes them, otherwise
  ``command_run`` facts. Exit codes arrive later from transcripts (decision 0020) and are joined
  by tool-use id, falling back to the same command within a short window.
- File-editing tools become ``file_change`` facts (one per path). Shell commands that look
  like writes add a ``file_change`` with subject ``<shell>`` so staleness stays conservative.
- Nothing here can produce ``arbiter_observed``: that origin is reserved for Arbiter's own reads
  and ``arbiter verify``.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

from arbiter_agent.telemetry import errors as err
from arbiter_agent.telemetry.runner_parsers import detect, fingerprint

SHELL_TOOLS = {"bash", "powershell", "exec_command", "exec", "shell", "local_shell", "container.exec", "run_command",
               "terminal", "execute_command", "run_terminal_cmd", "run_in_terminal", "runinterminal",
               "run_shell_command"}
EDIT_TOOLS = {"edit", "write", "multiedit", "notebookedit", "apply_patch", "str_replace_based_edit_tool",
              "str_replace_editor", "create_file", "write_file", "edit_file", "replace_in_file",
              "insert_edit_into_file", "replace_string_in_file", "search_replace", "multi_replace_string_in_file"}
PATCH_PATH = re.compile(r"\*\*\* (?:Update|Add|Delete) File: ([^\n\\\"]+)")
MOVE_PATH = re.compile(r"\*\*\* Move to: ([^\n\\\"]+)")
SHELL_WRITE = re.compile(
    r"(?:^|[\s;&|(])(?:rm|del|mv|move|cp|copy|touch|mkdir|rmdir|tee|truncate|patch|unzip|tar)\s|\bsed\s+-i|"
    r"\bperl\s+-p?i|\bgit\s+(?:checkout|restore|reset|apply|stash|mv|rm|am|cherry-pick|revert|merge|rebase|pull)\b|"
    r"(?<![<>=\d-])>{1,2}\s*[^\s&|>]|\b(?:Set-Content|Add-Content|Out-File|Remove-Item|New-Item|Move-Item|"
    r"Copy-Item|Rename-Item|Clear-Content)\b|\bnpm\s+(?:install|i|uninstall|update)\b|\bpip\s+install\b|"
    r"\bdotnet\s+new\b|\bcargo\s+(?:add|fix)\b|\bprettier\s+--write\b|"
    r"\bblack\b|\bruff\s+(?:format|check\s+--fix)\b|\beslint\s+--fix\b|\bgo\s+(?:fmt|mod\s+tidy)\b",
    re.I)
# Inline interpreter code (python -c, node -e) counts as a write only when it calls something that
# writes. Treating every one-liner as a write made each verification snippet stale all earlier
# test evidence (seen in real Claude Code benchmark runs, 2026-09-25).
INLINE_CODE = re.compile(r"\b(?:python\S*|py)\s+(?:-\w+\s+)*-c\b|\bnode\s+(?:-\w+\s+)*(?:-e|--eval)\b", re.I)
INLINE_WRITE = re.compile(
    r"\bopen\([^)]*['\"][^'\"]*[wax+]|\.write(?:_text|_bytes|lines)?\(|\bos\.(?:remove|unlink|rename|replace|"
    r"makedirs|mkdir|rmdir|system|truncate)|\bshutil\.|\.(?:unlink|rename|replace|mkdir|rmdir|touch)\(|"
    r"\bsubprocess\b|\bPopen\b|writeFile|appendFile|\bfs\.(?:write|append|unlink|rm|rename|mkdir|copy)|child_process",
    re.I)
# A shell wrapper runs its quoted script, so redirects inside those quotes are real.
SHELL_WRAPPER = re.compile(r"\b(?:bash|sh|zsh|pwsh|powershell(?:\.exe)?|cmd(?:\.exe)?)\s+(?:-\w+\s+)*"
                           r"(?:-c|-command|/c)\b", re.I)
QUOTED = re.compile(r"\"(?:[^\"\\]|\\.)*\"|'[^']*'")

UNAVAILABLE = re.compile(r"command not found|is not recognized as (?:an internal or external command|the name of a "
                         r"cmdlet)|No module named (?:pytest|unittest)|executable file not found|ENOENT", re.I)
DOC_PATH = re.compile(r"\.(md|mdx|rst|txt|adoc)$|(^|/)(docs?|documentation)/|(^|/)(CHANGELOG|LICENSE|AUTHORS)[^/]*$",
                      re.I)
JOIN_WINDOW_S = 300.0


@dataclass
class FactDraft:
    kind: str
    origin: str
    subject: str | None
    status: str | None
    data: dict[str, Any] = field(default_factory=dict)
    tool_use_id: str | None = None


def _shell_command(tool_input: Any) -> str:
    if isinstance(tool_input, dict):
        cmd = tool_input.get("command", tool_input.get("cmd", tool_input.get("script", "")))
    else:
        cmd = tool_input
    if isinstance(cmd, list):
        # Codex exec style: ["bash", "-lc", "pytest -q"] or ["powershell.exe", "-Command", "..."]
        if len(cmd) >= 3 and str(cmd[1]).lower() in ("-lc", "-c", "-command", "/c"):
            return str(cmd[2])
        return " ".join(str(c) for c in cmd)
    return str(cmd or "")


def _output_text(resp: Any) -> tuple[str, bool | None, bool]:
    """(combined output, is_error if known, interrupted)."""
    if isinstance(resp, dict):
        parts = [str(resp.get(k) or "") for k in ("stdout", "stderr", "output", "result", "content") if resp.get(k)]
        is_err = resp.get("is_error")
        if is_err is None and "exit_code" in resp and isinstance(resp.get("exit_code"), int):
            is_err = resp["exit_code"] != 0
        return "\n".join(parts), None if is_err is None else bool(is_err), bool(resp.get("interrupted"))
    if isinstance(resp, list):
        return "\n".join(str(c.get("text", "")) if isinstance(c, dict) else str(c) for c in resp), None, False
    return str(resp or ""), None, False


def edit_paths(tool_name: str, tool_input: Any) -> list[str]:
    paths: list[str] = []
    if isinstance(tool_input, dict):
        for k in ("file_path", "path", "notebook_path", "filename", "target_file"):
            v = tool_input.get(k)
            if isinstance(v, str) and v:
                paths.append(v)
        for e in tool_input.get("edits") or []:
            if isinstance(e, dict) and isinstance(e.get("file_path"), str):
                paths.append(e["file_path"])
    blob = tool_input if isinstance(tool_input, str) else json.dumps(tool_input, ensure_ascii=False)
    if "*** " in blob:
        blob = blob.replace("\\n", "\n")
        paths += [p.strip() for p in PATCH_PATH.findall(blob)] + [p.strip() for p in MOVE_PATH.findall(blob)]
    seen: list[str] = []
    for p in paths:
        if p not in seen:
            seen.append(p)
    return seen[:50]


def shell_writes(command: str) -> bool:
    """Whether a shell command may change files (conservative outside quoted inline code)."""
    if SHELL_WRITE.search(command if SHELL_WRAPPER.search(command) else QUOTED.sub('""', command)):
        return True
    return bool(INLINE_CODE.search(command) and INLINE_WRITE.search(command))


def shell_result(command: str, output: str, *, exit_code: int | None, is_error: bool | None, interrupted: bool,
                 origin: str, tool_use_id: str | None) -> list[FactDraft]:
    fp = fingerprint(command)
    if not fp:
        return []
    known_exit = exit_code if exit_code is not None else (None if is_error is None else (1 if is_error else 0))
    res = detect(command, output, known_exit)
    data: dict[str, Any] = {"command": command[:500], "exit_code": exit_code, "is_error": is_error}
    if interrupted:
        data["interrupted"] = True
    fps = err.fingerprints(output) if (res is None or res.status != "pass") else []
    if fps:
        data["error_fps"] = fps
    if UNAVAILABLE.search(output or "") and (known_exit is None or known_exit != 0):
        data["unavailable"] = True
    out: list[FactDraft] = []
    if res is not None:
        data.update(runner=res.runner, runner_kind=res.kind, passed=res.passed, failed=res.failed, errors=res.errors,
                    skipped=res.skipped, total=res.total, parser_version=res.parser_version, note=res.note)
        status = "unknown" if interrupted else res.status
        out.append(FactDraft("test_run", origin, fp, status, data, tool_use_id))
    else:
        status = "unknown" if known_exit is None or interrupted else ("pass" if known_exit == 0 else "fail")
        out.append(FactDraft("command_run", origin, fp, status, data, tool_use_id))
        if shell_writes(command):
            out.append(FactDraft("file_change", origin, "<shell>", "n/a", {"via": "shell", "command": fp[:200]},
                                 f"{tool_use_id}:w" if tool_use_id else None))
    return out


def from_hook(event_type: str, payload: dict[str, Any]) -> list[FactDraft]:
    """Facts from a PostToolUse-style hook payload (host_reported)."""
    if event_type != "post_tool":
        return []
    name = str(payload.get("tool_name") or "")
    tid = payload.get("tool_use_id")
    tin = payload.get("tool_input")
    lname = name.lower()
    if lname in SHELL_TOOLS:
        output, is_err, interrupted = _output_text(payload.get("tool_response"))
        exit_code = None
        resp = payload.get("tool_response")
        if isinstance(resp, dict) and isinstance(resp.get("exit_code"), int):
            exit_code = resp["exit_code"]
        return shell_result(_shell_command(tin), output, exit_code=exit_code, is_error=is_err,
                            interrupted=interrupted, origin="host_reported", tool_use_id=tid)
    if lname in EDIT_TOOLS or (lname.startswith("mcp__") and any(w in lname for w in ("write", "edit", "patch"))):
        return [FactDraft("file_change", "host_reported", p, "n/a", {"tool": name[:60]},
                          f"{tid}:{i}" if tid else None) for i, p in enumerate(edit_paths(name, tin))]
    return []


def from_transcript_call(payload: dict[str, Any]) -> tuple[str, str, Any]:
    """(tool name, call id, parsed input) from a transcript tool_call record."""
    name = str(payload.get("name") or "")
    raw = payload.get("input", payload.get("arguments"))
    parsed: Any = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = raw
    return name, str(payload.get("call_id") or ""), parsed


def insert(conn: sqlite3.Connection, session_id: str, epoch: int, d: FactDraft, seq: int | None,
           now: float | None = None) -> int | None:
    now = now or time.time()
    cur = conn.execute(
        "INSERT OR IGNORE INTO fact(session_id, goal_epoch, kind, origin, subject, status, tool_use_id, data_json, "
        "source_seq, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (session_id, epoch, d.kind, d.origin, d.subject, d.status, d.tool_use_id,
         json.dumps(d.data, default=str), seq, now, now))
    return int(cur.lastrowid or 0) if cur.rowcount else None


def find_joinable(conn: sqlite3.Connection, session_id: str, tool_use_id: str | None, subject: str | None,
                  now: float) -> sqlite3.Row | None:
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    if tool_use_id:
        row = cur.execute("SELECT * FROM fact WHERE session_id = ? AND tool_use_id = ? AND kind IN "
                           "('test_run','command_run') LIMIT 1", (session_id, tool_use_id)).fetchone()
        if row:
            return row
    if subject:
        row = cur.execute(
            "SELECT * FROM fact WHERE session_id = ? AND subject = ? AND kind IN ('test_run','command_run') "
            "AND created_at >= ? AND json_extract(data_json, '$.exit_code') IS NULL "
            "AND json_extract(data_json, '$.joined') IS NULL ORDER BY id DESC LIMIT 1",
            (session_id, subject, now - JOIN_WINDOW_S)).fetchone()
        return row
    return None


def apply_join(conn: sqlite3.Connection, row: sqlite3.Row, draft: FactDraft, now: float) -> None:
    """Merge a transcript result (with exit status) into an earlier hook fact."""
    data = json.loads(row["data_json"] or "{}")
    for k in ("exit_code", "is_error", "unavailable", "interrupted"):
        if draft.data.get(k) is not None:
            data[k] = draft.data[k]
    if draft.data.get("error_fps") and not data.get("error_fps"):
        data["error_fps"] = draft.data["error_fps"]
    data["joined"] = True
    status = row["status"]
    kind = row["kind"]
    if draft.kind == "test_run" and kind == "command_run":
        kind = "test_run"
        for k in ("runner", "runner_kind", "passed", "failed", "errors", "skipped", "total", "parser_version", "note"):
            data[k] = draft.data.get(k)
        status = draft.status
    elif kind == "test_run":
        if draft.kind == "test_run":
            status = draft.status if draft.status != "unknown" or status == "pass" else status
            if status == "pass" and draft.status == "unknown":
                data["note"] = draft.data.get("note") or "exit status contradicts parsed pass"
        exit_bad = (isinstance(draft.data.get("exit_code"), int) and draft.data["exit_code"] != 0) or \
            draft.data.get("is_error") is True
        if status == "pass" and exit_bad:
            status, data["note"] = "unknown", "parsed pass contradicts the exit status"
    else:
        status = draft.status if draft.status != "unknown" else status
    conn.execute("UPDATE fact SET kind = ?, status = ?, data_json = ?, updated_at = ? WHERE id = ?",
                 (kind, status, json.dumps(data, default=str), now, row["id"]))


def load(conn: sqlite3.Connection, session_id: str, kinds: tuple[str, ...] | None = None,
         epoch: int | None = None) -> list[dict[str, Any]]:
    q = "SELECT id, goal_epoch, kind, origin, subject, status, tool_use_id, data_json, source_seq, created_at " \
        "FROM fact WHERE session_id = ?"
    args: list[Any] = [session_id]
    if kinds:
        q += f" AND kind IN ({','.join('?' * len(kinds))})"
        args += list(kinds)
    if epoch is not None:
        q += " AND goal_epoch = ?"
        args.append(epoch)
    q += " ORDER BY COALESCE(source_seq, 0), id"
    out = []
    for r in conn.execute(q, args).fetchall():
        data = json.loads(r[7] or "{}")
        out.append({"id": r[0], "goal_epoch": r[1], "kind": r[2], "origin": r[3], "subject": r[4], "status": r[5],
                    "tool_use_id": r[6], "data": data, "source_seq": r[8] or 0, "created_at": r[9],
                    **{k: data.get(k) for k in ("passed", "failed", "errors", "total")}})
    return out


def is_doc_path(path: str) -> bool:
    return bool(DOC_PATH.search(path.replace("\\", "/")))
