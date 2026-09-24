"""Minimal mock of the OpenAI Responses API (streaming) for zero-cost Codex spikes.

MOCK_REPLIES   '|'-separated assistant texts, cycled per text response.
MOCK_TOOL_CALL 'name::json-args' -> first response in a turn is that function call;
               once a function_call_output is present, a text reply is returned.
MOCK_LOG       jsonl request log.
"""
import itertools
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPLIES = itertools.cycle(os.environ.get("MOCK_REPLIES", "Done. All tests pass.").split("|"))
LOG = os.environ.get("MOCK_LOG")
USAGE = {"input_tokens": 100, "input_tokens_details": {"cached_tokens": 80},
         "output_tokens": 10, "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 110}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({"object": "list", "data": [{"id": "mock-model", "object": "model"}]}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _ev(self, t, d):
        d["type"] = t
        self.wfile.write(("event: %s\ndata: %s\n\n" % (t, json.dumps(d))).encode())
        self.wfile.flush()

    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(n)
        try:
            req = json.loads(raw)
        except Exception:
            req = {}
        inp = req.get("input", []) or []
        tools = [t.get("name") or t.get("type") for t in (req.get("tools") or [])]
        if LOG:
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({"path": self.path, "model": req.get("model"), "reasoning": req.get("reasoning"),
                                    "n_input": len(inp), "tools": tools, "input_tail": inp[-2:]}) + "\n")
        rid = "resp_%d" % int(time.time() * 1000)
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        self._ev("response.created", {"response": {"id": rid, "status": "in_progress", "output": []}})

        has_output = any(isinstance(i, dict) and i.get("type") == "function_call_output" for i in inp)
        tool = os.environ.get("MOCK_TOOL_CALL")
        if tool and not has_output:
            name, args = tool.split("::", 1)
            item = {"type": "function_call", "id": "fc_" + rid, "call_id": "call_" + rid,
                    "name": name, "arguments": args, "status": "completed"}
        else:
            text = next(REPLIES)
            item = {"type": "message", "id": "msg_" + rid, "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": text, "annotations": []}]}
        self._ev("response.output_item.added", {"output_index": 0, "item": item})
        if item["type"] == "message":
            self._ev("response.output_text.delta", {"output_index": 0, "content_index": 0,
                                                    "item_id": item["id"], "delta": item["content"][0]["text"]})
        self._ev("response.output_item.done", {"output_index": 0, "item": item})
        self._ev("response.completed", {"response": {"id": rid, "status": "completed", "output": [item], "usage": USAGE}})


if __name__ == "__main__":
    port = int(sys.argv[1])
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    print("mock on %d" % port, flush=True)
    srv.serve_forever()
