"""In-process replay harness (M3.8): the real ingest pipeline and session engine on a throwaway
store, with no IPC, daemon or client processes. Used by the eval corpus and by tests.

A trace is a list of steps:
    {"hook": "UserPromptSubmit", "payload": {...}}              -> ingest a hook, return its decision
    {"transcript": "tool_result", "record": {...}, ...}          -> ingest a transcript record
    {"tool": "contract_propose" | "scope_change" | "finish_check" | "decide" | "verify_submit", ...}
    {"write": "path", "text": "..."} / {"delete": "path"}        -> change the workspace
    {"baseline": true}                                           -> capture the session baseline now
"""

from __future__ import annotations

import os
import secrets
import shutil
import tempfile
from pathlib import Path
from typing import Any

from arbiter_agent.audit.replay import Reducer
from arbiter_agent.clients.ingest import Ingestor
from arbiter_agent.concurrency import Deadline
from arbiter_agent.config.loader import build_config
from arbiter_agent.daemon.session_engine import SessionEngine
from arbiter_agent.flags import FeatureFlags
from arbiter_agent.privacy.scope import ProjectScope
from arbiter_agent.state.store import migrate
from arbiter_agent.state.writer import Writer


class Harness:
    def __init__(self, workdir: Path | None = None, config: dict[str, Any] | None = None, *,
                 client: str = "claude_code", session: str = "s1", background: bool = False) -> None:
        self._own = workdir is None
        self.dir = Path(workdir or tempfile.mkdtemp(prefix="arbiter-eval-"))
        self.repo = self.dir / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        (self.dir / "store").mkdir(exist_ok=True)
        self.db = self.dir / "store" / "arbiter.db"
        res = migrate(self.db, self.dir / "store" / "backups")
        if res.degraded:
            if self._own:
                shutil.rmtree(self.dir, ignore_errors=True)
            raise RuntimeError(f"migration failed: {res.error}")
        self.config = build_config(config or {})
        self.writer = Writer(self.db).start()
        self.reducer = Reducer()
        self.scope = ProjectScope(self.dir / "store" / "scope.json")
        self.ingestor = Ingestor(key=secrets.token_bytes(32), writer=self.writer, reducer=self.reducer,
                                 scope=self.scope)
        self.engine = SessionEngine(db=self.db, writer=self.writer, keyer=self.ingestor.keyer, config=self.config,
                                    reducer=self.reducer, background=background,
                                    flags=FeatureFlags(self.config.get("features") or {}))
        if background:
            self.engine.bg.start()
        self.client = client
        self.session = session
        self.responses: list[dict[str, Any]] = []
        self.results: list[Any] = []

    @property
    def sid(self) -> str:
        return f"{self.client}:{self.session}"

    def close(self) -> None:
        self.engine.bg.stop()
        self.writer.stop()
        if self._own:
            shutil.rmtree(self.dir, ignore_errors=True)

    def __enter__(self) -> Harness:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ steps
    def _common(self, session: str | None = None) -> dict[str, Any]:
        return {"session_id": session or self.session, "cwd": str(self.repo),
                "transcript_path": str(self.dir / "t.jsonl")}

    def hook(self, event: str, payload: dict[str, Any] | None = None, *, session: str | None = None,
             client: str | None = None) -> dict[str, Any]:
        body = {"hook_event_name": event, **self._common(session), **(payload or {})}
        c = client or self.client
        res = self.ingestor.ingest_hook(c, body, surface="hook", wait=5.0)
        resp = self.engine.after_hook(c, body, event, Deadline(5.0)) if res.status in ("stored", "duplicate") else {}
        self.responses.append(resp)
        return resp

    def prompt(self, text: str, **kw: Any) -> dict[str, Any]:
        return self.hook("UserPromptSubmit", {"prompt": text}, **kw)

    def shell(self, command: str, output: str, *, tool_use_id: str | None = None, exit_code: int | None = None,
              is_error: bool | None = None, session: str | None = None) -> None:
        tid = tool_use_id or f"t{secrets.token_hex(4)}"
        resp: dict[str, Any] = {"stdout": output, "stderr": "", "interrupted": False}
        self.hook("PostToolUse", {"tool_name": "Bash", "tool_input": {"command": command}, "tool_response": resp,
                                  "tool_use_id": tid}, session=session)
        if exit_code is not None or is_error is not None:
            rec: dict[str, Any] = {"stdout": output, "stderr": ""}
            if exit_code is not None:
                rec.update(command=command, exit_code=exit_code)
            if is_error is not None:
                rec["is_error"] = is_error
            self.ingestor.ingest_transcript_record(self.client, rec, session_id=session or self.session,
                                                   record_id=f"r{tid}", record_type="tool_result", tool_use_id=tid)

    def edit(self, path: str, text: str | None = None, *, session: str | None = None, delete: bool = False) -> None:
        p = self.repo / path
        if delete:
            if p.exists():
                p.unlink()
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text or "", encoding="utf-8")
        tid = f"e{secrets.token_hex(4)}"
        self.hook("PostToolUse", {"tool_name": "Write", "tool_input": {"file_path": str(p), "content": "…"},
                                  "tool_response": {"success": True}, "tool_use_id": tid}, session=session)

    def write(self, path: str, text: str) -> None:
        """Change the workspace without a hook (setup before the session, or outside the agent)."""
        p = self.repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def stop(self, message: str, *, session: str | None = None) -> dict[str, Any]:
        return self.hook("Stop", {"last_assistant_message": message, "stop_hook_active": False}, session=session)

    def drain(self) -> None:
        self.engine.drain(10.0)

    def baseline(self, session: str | None = None) -> dict[str, Any]:
        self.drain()
        sid = f"{self.client}:{session or self.session}"
        return self.engine.capture_baseline_now(sid, str(self.repo))

    def propose(self, contracts: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
        self.drain()
        return self.engine.propose(self.sid, contracts, **kw)

    def ledger(self, session: str | None = None) -> Any:
        self.drain()
        return self.engine.evaluate(f"{self.client}:{session or self.session}")[1]

    # ------------------------------------------------------------------ trace runner
    def run(self, trace: list[dict[str, Any]]) -> list[Any]:
        out: list[Any] = []
        for step in trace:
            out.append(self.step(step))
        self.drain()
        return out

    def step(self, s: dict[str, Any]) -> Any:
        if "hook" in s:
            return self.hook(s["hook"], s.get("payload"), session=s.get("session"))
        if "prompt" in s:
            return self.prompt(s["prompt"], session=s.get("session"))
        if "shell" in s:
            return self.shell(s["shell"], s.get("output", ""), exit_code=s.get("exit_code"),
                              is_error=s.get("is_error"), session=s.get("session"))
        if "edit" in s:
            return self.edit(s["edit"], s.get("text"), delete=bool(s.get("delete")), session=s.get("session"))
        if "write" in s:
            return self.write(s["write"], s.get("text", ""))
        if "baseline" in s:
            return self.baseline(s.get("session"))
        if "stop" in s:
            return self.stop(s["stop"], session=s.get("session"))
        if "tool" in s:
            self.drain()
            sid = f"{self.client}:{s.get('session') or self.session}"
            t = s["tool"]
            if t == "contract_propose":
                return self.engine.propose(sid, s["contracts"], supersedes=s.get("supersedes"),
                                           new_task=bool(s.get("new_task")))
            if t == "scope_change":
                return self.engine.scope_change(sid, s.get("summary", ""), s.get("carry"))
            if t == "finish_check":
                return self.engine.finish_check(sid, s.get("summary", ""), s.get("claims"))
            if t == "decide":
                return self.engine.decide_contract(sid, s["contract"], s["decision"])
            if t == "verify_submit":
                return self.engine.submit_verification(sid, s["results"], "test")
            if t == "ack_integrity":
                return self.engine.ack_integrity(sid, s["keys"])
            if t == "gate_mode":
                return self.engine.set_gate_mode(sid, s["mode"])
        raise ValueError(f"unknown step {s}")


def git_init(repo: Path) -> bool:
    """Initialize a throwaway git repo with one commit (skips if git is missing)."""
    import subprocess

    env = dict(os.environ, GIT_AUTHOR_NAME="arbiter", GIT_AUTHOR_EMAIL="arbiter@example.invalid",
               GIT_COMMITTER_NAME="arbiter", GIT_COMMITTER_EMAIL="arbiter@example.invalid")
    try:
        for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "base", "--allow-empty"]):
            subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env, timeout=30)
        return True
    except (OSError, subprocess.SubprocessError):
        return False
