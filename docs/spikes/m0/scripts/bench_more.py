import json, os, statistics, subprocess, sys, time, threading, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
HERE = os.path.dirname(os.path.abspath(__file__)); N = 30
PAYLOAD = json.dumps({"hook_event_name": "PostToolUse", "tool_response": "." * 800}).encode()
TRAMP = os.path.join(HERE, "latpkg", ".venv", "Scripts", "arbiter-spike-hook.exe")
BASH = r"D:\Git\bin\bash.exe" if os.path.exists(r"D:\Git\bin\bash.exe") else r"D:\Git\usr\bin\bash.exe"
def stats(xs):
    xs = sorted(xs); return {"p50": round(statistics.median(xs), 1), "p95": round(xs[int(0.95 * (len(xs) - 1))], 1)}
def run(cmd):
    t = time.perf_counter(); subprocess.run(cmd, input=PAYLOAD, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); return (time.perf_counter() - t) * 1000
res = {}
for name, cmd in {"git-bash -c exit": [BASH, "-c", "exit"],
                  "CLAUDE shell form: git-bash -c <trampoline>": [BASH, "-c", TRAMP.replace("\\", "/")],
                  "CLAUDE exec form: trampoline direct": [TRAMP]}.items():
    for _ in range(3): run(cmd)
    res[name] = stats([run(cmd) for _ in range(N)]); print(f"{name:48s} {res[name]}", flush=True)
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        self.rfile.read(int(self.headers["content-length"])); b = b"{}"
        self.send_response(200); self.send_header("content-length", "2"); self.end_headers(); self.wfile.write(b)
srv = ThreadingHTTPServer(("127.0.0.1", 18799), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
def post():
    t = time.perf_counter()
    urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:18799/h", data=PAYLOAD, headers={"content-type": "application/json"})).read()
    return (time.perf_counter() - t) * 1000
for _ in range(5): post()
res["HTTP hook round-trip (localhost POST, server in daemon)"] = stats([post() for _ in range(200)])
print(f"{'HTTP hook round-trip (localhost POST)':48s} {res['HTTP hook round-trip (localhost POST, server in daemon)']}")
res["_bash"] = BASH
prev = json.load(open(os.path.join(HERE, "logs", "bench_hooks.json")))
prev["results"].update(res); json.dump(prev, open(os.path.join(HERE, "logs", "bench_hooks.json"), "w"), indent=1)
