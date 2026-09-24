"""Spike M0.e: hook start-up latency on Windows. Each case gets a ~1 KB JSON payload on stdin."""
import json
import os
import shutil
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
N = int(os.environ.get("N", "40"))
PAYLOAD = json.dumps({"session_id": "x" * 36, "hook_event_name": "PostToolUse", "tool_name": "Bash",
                      "tool_input": {"command": "pytest -q"}, "tool_response": "." * 800}).encode()
PY = sys.executable
TRAMP = os.path.join(HERE, "latpkg", ".venv", "Scripts", "arbiter-spike-hook.exe")
PS51 = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
PWSH = shutil.which("pwsh")
BASH = r"C:\Program Files\Git\bin\bash.exe"
NATIVE = r"C:\Windows\System32\hostname.exe"

cases = {
    "native exe baseline (hostname.exe)": [NATIVE],
    "python -I -S -c pass": [PY, "-I", "-S", "-c", "pass"],
    "python -I (site) read stdin+json": [PY, "-I", "-c", "import sys,json;json.loads(sys.stdin.read())"],
    "uv venv console-script trampoline": [TRAMP],
    "powershell 5.1 -NoProfile -Command exit": [PS51, "-NoProfile", "-Command", "exit"],
    "CODEX PATH: ps5.1 -NoProfile -Command <trampoline>": [PS51, "-NoProfile", "-Command", TRAMP],
    "git-bash -c exit": [BASH, "-c", "exit"],
    "CLAUDE PATH (bash): bash -c <trampoline>": [BASH, "-c", TRAMP.replace("\\", "/")],
}
if PWSH:
    cases["pwsh 7 -NoProfile -Command exit"] = [PWSH, "-NoProfile", "-Command", "exit"]


def run(cmd):
    t = time.perf_counter()
    subprocess.run(cmd, input=PAYLOAD, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return (time.perf_counter() - t) * 1000


results = {}
for name, cmd in cases.items():
    if not os.path.exists(cmd[0]):
        results[name] = None
        continue
    for _ in range(3):
        run(cmd)  # warm file cache
    xs = sorted(run(cmd) for _ in range(N))
    results[name] = {"p50": round(statistics.median(xs), 1), "p95": round(xs[int(0.95 * (len(xs) - 1))], 1),
                     "max": round(xs[-1], 1)}
    print(f"{name:55s} p50={results[name]['p50']:7.1f}ms  p95={results[name]['p95']:7.1f}ms  max={results[name]['max']:7.1f}ms",
          flush=True)
json.dump({"n": N, "python": sys.version, "results": results}, open(os.path.join(HERE, "logs", "bench_hooks.json"), "w"), indent=1)
