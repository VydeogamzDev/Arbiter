"""HTTP hook sink for Claude Code spikes. Logs {ts, path, body}; blocks the first Stop once
(if HTTP_BLOCK_STOP=1) using the documented decision format."""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = os.environ["HTTP_HOOK_LOG"]
state = {"blocked": False}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        t = time.time()
        n = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(n)
        try:
            body = json.loads(raw)
        except Exception:
            body = {"_raw": raw.decode("utf-8", "replace")}
        out = {}
        if (body.get("hook_event_name") == "Stop" and os.environ.get("HTTP_BLOCK_STOP")
                and not body.get("stop_hook_active") and not state["blocked"]):
            state["blocked"] = True
            out = {"decision": "block", "reason": "ARBITER-SPIKE: before stopping, reply with the single word VERIFIED."}
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": t, "path": self.path, "response": out, "body": body}) + "\n")
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H)
    print("http hook sink on", sys.argv[1], flush=True)
    srv.serve_forever()
