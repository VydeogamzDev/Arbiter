import json
import logging
import time

from arbiter_agent.daemon import diagnostics
from arbiter_agent.privacy.redaction import Redactor


def test_json_logs_are_redacted(home):
    diagnostics.setup_logging(home, redactor=Redactor(b"k" * 32))
    diagnostics.log("daemon", logging.INFO, "saw sk-proj-" + "Q" * 30, token="ghp_" + "b" * 36)
    for h in logging.getLogger("arbiter").handlers:
        h.flush()
    text = (home.logs / diagnostics.LOG_FILE).read_text(encoding="utf-8")
    assert "sk-proj-QQQQ" not in text and "ghp_bbbb" not in text
    rec = json.loads(text.strip().splitlines()[-1])
    assert rec["component"] == "daemon" and rec["level"] == "info" and "[REDACTED:" in rec["msg"]
    assert diagnostics.iter_log_lines(home, "daemon")


def test_debug_mode_expires(home):
    assert not diagnostics.debug_active(home)
    until = diagnostics.set_debug(home, True, hours=24)
    assert diagnostics.debug_active(home)
    assert not diagnostics.debug_active(home, now=until + 1)  # auto-expiry
    diagnostics.set_debug(home, False)
    assert not diagnostics.debug_active(home)


def test_debug_raises_log_level(home):
    diagnostics.set_debug(home, True, hours=1)
    logger = diagnostics.setup_logging(home)
    assert logger.level == logging.DEBUG
    diagnostics.set_debug(home, False)
    diagnostics.refresh_level(home)
    assert logger.level == logging.INFO


def test_failopen_record_never_raises(home, tmp_path):
    diagnostics.record_failopen(home.logs, "hook_cli", "daemon unreachable")
    line = json.loads((home.logs / diagnostics.FAILOPEN_FILE).read_text().strip())
    assert line["component"] == "hook_cli" and line["ts"] <= time.time()
    diagnostics.record_failopen(tmp_path / "file-not-dir" / "\0bad", "x", "y")  # must not raise
