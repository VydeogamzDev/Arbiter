"""Tiny stdio MCP server for spikes: exposes `hook_event`, logs every call with timestamps.
Log path: MINI_MCP_LOG. Optional MINI_MCP_BLOCK_STOP=1 returns a Stop block decision once."""
import json
import os
import sys
import time

LOG = os.environ.get("MINI_MCP_LOG", os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "mini_mcp.jsonl"))
blocked = {"done": False}


def log(obj):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.time(), "pid": os.getpid(), **obj}) + "\n")


def reply(mid, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}) + "\n")
    sys.stdout.flush()


log({"event": "server_start", "argv": sys.argv})
for line in sys.stdin:
    try:
        msg = json.loads(line)
    except Exception:
        continue
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        reply(mid, {"protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-06-18"),
                    "capabilities": {"tools": {}}, "serverInfo": {"name": "arbiter-spike", "version": "0.0.1"}})
    elif method == "tools/list":
        reply(mid, {"tools": [{"name": "hook_event", "description": "Arbiter spike hook sink",
                               "inputSchema": {"type": "object", "additionalProperties": True}}]})
    elif method == "tools/call":
        args = msg.get("params", {}).get("arguments", {})
        log({"event": "tools/call", "name": msg["params"].get("name"), "arguments": args})
        text = ""
        ev = args.get("hook_event_name") if isinstance(args, dict) else None
        if ev == "Stop" and os.environ.get("MINI_MCP_BLOCK_STOP") and not args.get("stop_hook_active") and not blocked["done"]:
            blocked["done"] = True
            text = json.dumps({"decision": "block", "reason": "ARBITER-SPIKE via mcp_tool: verify once more."})
        reply(mid, {"content": [{"type": "text", "text": text}], "isError": False})
    elif mid is not None:
        reply(mid, {})
