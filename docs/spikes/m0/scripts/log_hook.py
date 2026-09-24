"""Hook logger: appends {event, argv, ts, payload} to HOOK_LOG. Optional behaviors via env."""
import json, os, sys, time
t0 = time.time()
raw = sys.stdin.read()
try: payload = json.loads(raw)
except Exception: payload = {"_raw": raw}
event = sys.argv[1] if len(sys.argv) > 1 else payload.get("hook_event_name")


def _image(pid):
    try:
        import ctypes
        from ctypes import wintypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        ok = k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        k.CloseHandle(h)
        return buf.value if ok else None
    except Exception:
        return None


def _in_job():
    try:
        import ctypes
        res = ctypes.c_int(0)
        ctypes.windll.kernel32.IsProcessInJob(ctypes.windll.kernel32.GetCurrentProcess(), None, ctypes.byref(res))
        return bool(res.value)
    except Exception:
        return None


def _cmdline(pid):
    try:
        import subprocess
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip()
    except Exception as e:
        return str(e)


if os.environ.get("HOOK_CMDLINE_PROBE") and event == "SessionStart":
    payload = {"_parent_cmdline": _cmdline(os.getppid()), **payload}

if os.environ.get("HOOK_ENV_PROBE"):
    payload = {"_parent_image": _image(os.getppid()), "_in_job": _in_job(),
               "_env_keys": sorted(k for k in os.environ if k.upper().startswith(("CODEX", "CLAUDE"))), **payload}
log = os.environ.get("HOOK_LOG") or os.path.join(os.path.dirname(__file__), "logs", "hooks.jsonl")
with open(log, "a", encoding="utf-8") as f:
    f.write(json.dumps({"event": event, "ts": t0, "pid": os.getpid(), "ppid": os.getppid(), "payload": payload}) + "\n")
# Spawn a detached survivor once per run (spike d)
if event == "SessionStart" and os.environ.get("SPAWN_SURVIVOR"):
    import subprocess
    flags = 0x00000008 | 0x00000200 | 0x01000000  # DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP|CREATE_BREAKAWAY_FROM_JOB
    child = [sys.executable, os.path.join(os.path.dirname(__file__), "survivor.py"), os.environ["SPAWN_SURVIVOR"]]
    try:
        subprocess.Popen(child, creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:  # breakaway not permitted -> retry without it
        with open(log, "a") as f: f.write(json.dumps({"event": "survivor_breakaway_error", "err": str(e)}) + "\n")
        subprocess.Popen(child, creationflags=flags & ~0x01000000, close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
# Block the first Stop once (continuation test)
if event == "Stop" and os.environ.get("BLOCK_FIRST_STOP") and not payload.get("stop_hook_active"):
    print(json.dumps({"decision": "block", "reason": "ARBITER-SPIKE: please verify once more."}))
