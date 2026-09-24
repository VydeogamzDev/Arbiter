"""Arbiter's own diagnostics (spec §16.6).

- Structured JSON-lines logs in the platform log directory, size-rotated, redacted.
- ``arbiter debug on|off``: raises verbosity; expires automatically (default 24h).
- Shims record a single local line when they fail open (``failopen.log``), stdlib only.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import time
from pathlib import Path
from typing import Any

from arbiter_agent.paths import ArbiterPaths, write_private

LOG_FILE = "arbiter.log"
FAILOPEN_FILE = "failopen.log"


class JsonFormatter(logging.Formatter):
    def __init__(self, redactor: Any | None = None) -> None:
        super().__init__()
        self.redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        extra = getattr(record, "fields", None)
        if self.redactor is not None:
            msg = self.redactor.redact_text(msg)
            if extra:
                extra = self.redactor.redact(extra)
        out: dict[str, Any] = {
            "ts": round(record.created, 3), "level": record.levelname.lower(),
            "component": record.name.removeprefix("arbiter."), "msg": msg, "pid": record.process,
        }
        if extra:
            out["fields"] = extra
        if record.exc_info:
            text = self.formatException(record.exc_info)
            out["exc"] = self.redactor.redact_text(text) if self.redactor is not None else text
        return json.dumps(out, ensure_ascii=False, default=str)


def debug_active(paths: ArbiterPaths, now: float | None = None) -> bool:
    try:
        data = json.loads(paths.debug_file.read_text(encoding="utf-8"))
        return float(data.get("until", 0)) > (now or time.time())
    except (OSError, ValueError):
        return False


def set_debug(paths: ArbiterPaths, on: bool, hours: float = 24.0) -> float | None:
    if not on:
        try:
            paths.debug_file.unlink()
        except FileNotFoundError:
            pass
        return None
    until = time.time() + hours * 3600
    write_private(paths.debug_file, json.dumps({"until": until}).encode())
    return until


def setup_logging(paths: ArbiterPaths, *, redactor: Any | None = None, rotation_mb: float = 20,
                  backups: int = 5, stderr: bool = False) -> logging.Logger:
    paths.logs.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("arbiter")
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    handler = logging.handlers.RotatingFileHandler(paths.logs / LOG_FILE, maxBytes=int(rotation_mb * 1024 * 1024),
                                                   backupCount=backups, encoding="utf-8")
    handler.setFormatter(JsonFormatter(redactor))
    logger.addHandler(handler)
    if stderr:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(JsonFormatter(redactor))
        logger.addHandler(sh)
    logger.setLevel(logging.DEBUG if debug_active(paths) else logging.INFO)
    logger.propagate = False
    return logger


def refresh_level(paths: ArbiterPaths) -> None:
    logging.getLogger("arbiter").setLevel(logging.DEBUG if debug_active(paths) else logging.INFO)


def log(component: str, level: int, msg: str, **fields: Any) -> None:
    logging.getLogger(f"arbiter.{component}").log(level, msg, extra={"fields": fields} if fields else None)


def record_failopen(logs_dir: Path, component: str, reason: str) -> None:
    """One local line per fail-open (spec §16.6). Never raises."""
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"ts": round(time.time(), 3), "component": component, "reason": reason[:300],
                           "pid": os.getpid()})
        with open(logs_dir / FAILOPEN_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def iter_log_lines(paths: ArbiterPaths, component: str | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    files = sorted(paths.logs.glob(LOG_FILE + ".*"), reverse=True) + [paths.logs / LOG_FILE]
    for p in files:
        if not p.exists() or not p.name.startswith(LOG_FILE):
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if component and not str(rec.get("component", "")).startswith(component):
                continue
            out.append(rec)
    return out
