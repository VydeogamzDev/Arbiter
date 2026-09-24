import json
import subprocess
import sys

import yaml

from tests.conftest import cli_argv

CHECK_IMPORTS = """
import sys
import arbiter_agent.shims.hook_cli, arbiter_agent.shims.mcp_server, arbiter_agent.daemon.lifecycle
heavy = sorted(m for m in ("yaml", "sqlite3", "_sqlite3", "arbiter_agent.config.loader", "arbiter_agent.state.store")
               if m in sys.modules)
print(",".join(heavy))
"""


def test_shim_import_path_is_stdlib_only():
    """Hooks and the MCP shim start on every client event; they must not pull in yaml/sqlite."""
    out = subprocess.run([sys.executable, "-c", CHECK_IMPORTS], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""


def _arbiter(home, *args, **kw):
    return subprocess.run(cli_argv(home) + list(args), capture_output=True, text=True, **kw)


def test_config_validate_reports_errors(home):
    assert _arbiter(home, "config", "validate").returncode == 0
    home.config_file.write_text(yaml.safe_dump({"hooks": {"gating_deadline_ms": "fast"}}))
    out = _arbiter(home, "config", "validate")
    assert out.returncode == 2 and "hooks.gating_deadline_ms" in out.stderr


def test_scope_commands_round_trip(home, tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    assert _arbiter(home, "exclude", str(proj)).returncode == 0
    lists = json.loads(_arbiter(home, "scope").stdout)
    assert len(lists["exclude"]) == 1 and lists["mode"] == "all_except_excluded"
    assert _arbiter(home, "include", str(proj)).returncode == 0
    assert json.loads(_arbiter(home, "scope").stdout)["exclude"] == []


def test_status_when_down_and_debug_toggle(home):
    out = _arbiter(home, "status")
    assert out.returncode == 1 and "not running" in out.stdout
    assert "debug on until" in _arbiter(home, "debug", "on", "--hours", "1").stdout
    assert _arbiter(home, "debug", "status").stdout.strip() == "on"
    _arbiter(home, "debug", "off")
    assert _arbiter(home, "debug", "status").stdout.strip() == "off"


def test_version_and_paths(home):
    assert "arbiter-agent" in _arbiter(home, "version").stdout
    paths = json.loads(_arbiter(home, "paths").stdout)
    assert paths["root"] and paths["pipe"]
