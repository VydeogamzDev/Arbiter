"""Drive `codex app-server` (the process Codex desktop talks to) over stdio JSON-RPC.

Usage: python drive_appserver.py <codex.exe> <codex_home> <cwd> [--trust]
Prints hooks/list before/after trust, hook notifications, and turn completion.
"""
import json
import os
import subprocess
import sys
import threading
import time

codex, home, cwd = sys.argv[1:4]
TRUST = "--trust" in sys.argv
env = dict(os.environ, CODEX_HOME=home, MOCK_API_KEY="x")
p = subprocess.Popen([codex, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE, env=env, cwd=cwd, text=True, encoding="utf-8", bufsize=1)
msgs, lock, nid = [], threading.Lock(), [0]


def reader():
    for line in p.stdout:
        try:
            m = json.loads(line)
        except Exception:
            continue
        with lock:
            msgs.append(m)


threading.Thread(target=reader, daemon=True).start()
threading.Thread(target=lambda: [None for _ in p.stderr], daemon=True).start()


def send(method, params=None, notify=False):
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    if not notify:
        nid[0] += 1
        msg["id"] = nid[0]
    p.stdin.write(json.dumps(msg) + "\n")
    p.stdin.flush()
    return None if notify else nid[0]


def wait_id(i, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        with lock:
            for m in msgs:
                if m.get("id") == i and ("result" in m or "error" in m):
                    return m
        time.sleep(0.05)
    raise TimeoutError(i)


def call(method, params=None, timeout=30):
    return wait_id(send(method, params), timeout)


r = call("initialize", {"clientInfo": {"name": "arbiter_spike", "title": "Arbiter spike", "version": "0.0.0"}})
print("initialize:", "ok" if "result" in r else r)
send("initialized", notify=True)

hl = call("hooks/list", {"cwds": [cwd]})
entries = []
for grp in (hl.get("result") or {}).get("data", []) or []:
    for h in grp.get("hooks", []) or []:
        entries.append(h)
print("hooks/list count:", len(entries), "error:", hl.get("error"))
if entries:
    print("sample hook entry keys:", sorted(entries[0].keys()))
    print("trust statuses:", sorted({e.get("trustStatus") for e in entries}))

if TRUST and entries:
    edits = [{"keyPath": f"hooks.state.{json.dumps(e['key'])}.trusted_hash", "mergeStrategy": "upsert",
              "value": e["currentHash"]} for e in entries]
    w = call("config/batchWrite", {"edits": edits, "reloadUserConfig": True})
    print("batchWrite:", "ok" if "result" in w else w.get("error"))
    hl2 = call("hooks/list", {"cwds": [cwd]})
    st = [h.get("trustStatus") for g in hl2["result"].get("data", []) for h in g.get("hooks", [])]
    print("trust after:", sorted(set(st)))

ts = call("thread/start", {"cwd": cwd})
tid = ts["result"]["thread"]["id"]
print("thread:", tid)
ti = call("turn/start", {"threadId": tid, "input": [{"type": "text", "text": "hello from app-server"}]})
print("turn/start:", "ok" if "result" in ti else ti.get("error"))
end = time.time() + 60
done = False
while time.time() < end and not done:
    with lock:
        done = any(m.get("method") == "turn/completed" for m in msgs)
    time.sleep(0.1)
time.sleep(1.0)
with lock:
    for m in msgs:
        meth = m.get("method", "")
        if meth.startswith("hook/"):
            run = m["params"]["run"]
            print("  ", meth, run.get("eventName"), run.get("status"), "source=", run.get("source"), "ms=", run.get("durationMs"))
print("turn completed:", done)
p.stdin.close()
try:
    p.wait(timeout=10)
except Exception:
    p.kill()
