"""``arbiter verify`` (spec §12.8 item 3): run the repository's configured verification commands.

- Config: opt-in ``.arbiter/verify.yaml`` in the repo (or ``verify.<repo-name>.yaml`` under the
  Arbiter config dir for repos where the user doesn't want files).
- Trust: a config runs only after the user approves its exact content hash with
  ``arbiter verify --trust``, which requires an interactive terminal. Agents' shells are not
  interactive, so an agent can't approve a verify file it wrote.
- Runs in the foreground, in the caller's process (CLI or the client-spawned MCP shim), under
  the repository's normal permissions. Never speculative.
- Results are ``arbiter_observed`` facts.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from arbiter_agent.telemetry import errors as err
from arbiter_agent.telemetry.runner_parsers import detect, fingerprint, junit_xml

DEFAULT_TIMEOUT_S = 900
MAX_OUTPUT = 200_000
KINDS = ("test", "typecheck", "lint", "build")


class VerifyConfigError(ValueError):
    pass


@dataclass
class VerifyCommand:
    name: str
    run: str
    kind: str = "test"
    timeout_s: int = DEFAULT_TIMEOUT_S
    report: str | None = None
    shell: str | None = None


@dataclass
class VerifyResult:
    name: str
    command: str
    kind: str
    exit_code: int | None
    status: str
    duration_s: float
    runner: dict[str, Any] | None = None
    report: dict[str, Any] | None = None
    output_tail: str = ""
    error_fps: list[tuple[str, str]] = field(default_factory=list)
    timed_out: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def config_path(root: str | Path, config_dir: Path | None = None, rel: str = ".arbiter/verify.yaml") -> Path | None:
    p = Path(root) / rel
    if p.is_file():
        return p
    if config_dir is not None:
        alt = config_dir / f"verify.{Path(root).name}.yaml"
        if alt.is_file():
            return alt
    return None


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> list[VerifyCommand]:
    try:
        data = yaml.safe_load(path.read_text("utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise VerifyConfigError(f"can't read {path}: {exc}") from exc
    raw = data.get("commands") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw:
        raise VerifyConfigError("verify.yaml needs a non-empty 'commands' list")
    out: list[VerifyCommand] = []
    for i, c in enumerate(raw):
        if isinstance(c, str):
            c = {"run": c}
        if not isinstance(c, dict) or not isinstance(c.get("run"), str) or not c["run"].strip():
            raise VerifyConfigError(f"command #{i + 1} needs a 'run' string")
        kind = str(c.get("kind", "test"))
        if kind not in KINDS:
            raise VerifyConfigError(f"command #{i + 1}: kind must be one of {', '.join(KINDS)}")
        shell = c.get("shell")
        if shell is not None and shell not in ("cmd", "powershell", "pwsh", "bash", "sh"):
            raise VerifyConfigError(f"command #{i + 1}: shell must be cmd, powershell, pwsh, bash or sh")
        out.append(VerifyCommand(name=str(c.get("name") or f"check{i + 1}")[:60], run=c["run"].strip(), kind=kind,
                                 timeout_s=int(c.get("timeout_s", DEFAULT_TIMEOUT_S)),
                                 report=str(c["report"]) if c.get("report") else None, shell=shell))
    return out


def _argv(cmd: VerifyCommand) -> tuple[list[str] | str, bool]:
    if cmd.shell in ("powershell", "pwsh"):
        exe = "powershell.exe" if cmd.shell == "powershell" else "pwsh"
        return [exe, "-NoProfile", "-NonInteractive", "-Command", cmd.run], False
    if cmd.shell in ("bash", "sh"):
        return [cmd.shell, "-c", cmd.run], False
    return cmd.run, True   # platform default shell (cmd.exe on Windows, /bin/sh elsewhere)


def run_one(cmd: VerifyCommand, root: str | Path) -> VerifyResult:
    argv, use_shell = _argv(cmd)
    started = time.time()
    t0 = time.monotonic()
    env = dict(os.environ, ARBITER_VERIFY="1")
    timed_out = False
    try:
        proc = subprocess.run(argv, shell=use_shell, cwd=str(root), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=cmd.timeout_s, env=env)
        code: int | None = proc.returncode
        output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    except subprocess.TimeoutExpired as exc:
        code, timed_out = None, True
        output = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
    except OSError as exc:
        code, output = 127, f"failed to start: {exc}"
    dur = round(time.monotonic() - t0, 3)
    output = output[-MAX_OUTPUT:]
    parsed = detect(cmd.run, output, code)
    report = None
    if cmd.report:
        rp = Path(root) / cmd.report
        try:
            if rp.is_file() and rp.stat().st_mtime >= started - 1:
                rr = junit_xml(rp.read_text("utf-8", errors="replace"))
                report = rr.to_dict() if rr else None
        except OSError:
            report = None
    if timed_out:
        status = "unknown"
    elif cmd.kind == "test":
        best = report or (parsed.to_dict() if parsed else None)
        if best is None:
            status = "unknown"    # an exit code alone isn't a recognized test result
        else:
            status = best["status"]
            if status == "pass" and code != 0:
                status = "unknown"
    else:
        status = parsed.status if parsed else ("pass" if code == 0 else "fail")
        if status == "pass" and code != 0:
            status = "unknown"
    return VerifyResult(cmd.name, cmd.run, cmd.kind, code, status, dur, parsed.to_dict() if parsed else None, report,
                        output[-4000:], err.fingerprints(output) if status != "pass" else [], timed_out)


def run_all(cmds: list[VerifyCommand], root: str | Path, only: list[str] | None = None) -> list[VerifyResult]:
    return [run_one(c, root) for c in cmds if not only or c.name in only]


def result_fact(r: VerifyResult) -> dict[str, Any]:
    """Fact payload submitted to the daemon (origin arbiter_observed)."""
    runner = r.report or r.runner or {}
    kind = "test_run" if (r.runner or r.report) or r.kind == "test" else "command_run"
    return {"kind": kind, "subject": fingerprint(r.command), "status": r.status,
            "data": {"command": r.command, "exit_code": r.exit_code, "via": "arbiter_verify", "name": r.name,
                     "runner": runner.get("runner"), "runner_kind": r.kind if r.kind != "test" else "test",
                     "passed": runner.get("passed"), "failed": runner.get("failed"), "errors": runner.get("errors"),
                     "skipped": runner.get("skipped"), "total": runner.get("total"),
                     "duration_s": r.duration_s, "timed_out": r.timed_out, "error_fps": r.error_fps}}
