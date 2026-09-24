"""M2/M4 exit: `uv tool install` the built wheel into a throwaway tool dir, then set up sandboxed
clients with the installed `arbiter`, run doctor + eval, uninstall, stop. Writes a JSON report."""

import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

repo = Path(r"D:\Projects\Arbiter")
wheel = glob.glob(str(repo / "dist" / "arbiter_agent-*.whl"))[0]
base = Path(tempfile.mkdtemp(prefix="arbiter-toolcheck-"))
env = dict(os.environ)
env.update({
    "UV_TOOL_DIR": str(base / "tools"), "UV_TOOL_BIN_DIR": str(base / "bin"),
    "ARBITER_HOME": str(base / "arbiter-home"), "CODEX_HOME": str(base / "codex"),
    "CLAUDE_CONFIG_DIR": str(base / "claude"), "ARBITER_CLIENT_HOME": str(base),
})
for d in ("codex", "claude"):
    (base / d).mkdir(parents=True)
(base / "codex" / "config.toml").write_text('model = "gpt-5"\n', encoding="utf-8")
env["PATH"] = str(base / "bin") + os.pathsep + env["PATH"]
report: dict = {"wheel": Path(wheel).name, "steps": []}


def run(*args: str, timeout: int = 180) -> subprocess.CompletedProcess:
    t0 = time.time()
    p = subprocess.run(list(args), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout)
    report["steps"].append({"cmd": " ".join(Path(args[0]).name if i == 0 else a for i, a in enumerate(args)),
                            "rc": p.returncode, "s": round(time.time() - t0, 2),
                            "tail": (p.stdout + p.stderr).strip().splitlines()[-6:]})
    return p


try:
    r = run("uv", "tool", "install", wheel)
    exe = shutil.which("arbiter", path=str(base / "bin"))
    report["installed_exe_in_tool_bin"] = bool(exe) and str(base) in (exe or "")
    assert exe, "arbiter not installed"
    run(exe, "version")
    run(exe, "setup", "--yes", "--clients", "codex,claude_code")
    time.sleep(1)
    run(exe, "status")
    d = run(exe, "doctor", "--json", timeout=120)
    try:
        doc = json.loads(d.stdout)
        report["doctor_clients"] = {c["client"]: {"configured": c.get("configured"), "verified": c.get("verified")}
                                    for c in doc.get("clients", [])}
        report["doctor_daemon"] = {k: doc.get("daemon", {}).get(k) for k in ("running", "version", "events")}
    except ValueError:
        report["doctor_clients"] = None
    cfg = (base / "codex" / "config.toml").read_text(encoding="utf-8")
    report["codex_mcp_points_to_tool_exe"] = str(base / "bin").lower() in cfg.lower() or "tools" in cfg.lower()
    e = run(exe, "eval", timeout=300)
    report["eval_passed"] = e.returncode == 0
    run(exe, "uninstall", "--yes", "--clients", "codex,claude_code")
    report["codex_config_restored"] = (base / "codex" / "config.toml").read_text(encoding="utf-8") == 'model = "gpt-5"\n'
    run(exe, "daemon", "stop")
finally:
    subprocess.run([shutil.which("arbiter", path=str(base / "bin")) or "arbiter", "daemon", "stop"], env=env,
                   capture_output=True, timeout=60) if (base / "bin").exists() else None
    subprocess.run(["uv", "tool", "uninstall", "arbiter-agent"], env=env, capture_output=True, timeout=120)
    out = repo / "docs" / "spikes" / "m4" / "tool_install_check.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report, indent=1))
    shutil.rmtree(base, ignore_errors=True)
