"""A tiny stdio MCP server for gateway tests. Tools:
- ``read_note`` (readOnlyHint), ``whoami`` (readOnlyHint): report env/cwd so tests can check scope;
- ``write_note`` (may change things), ``delete_all`` (destructive).
``FAKE_READONLY=1`` exposes only the read-only tools. On initialize it also sends the client a
``roots/list`` request, which a gateway must refuse (it offers the server nothing extra)."""

import json
import os
import sys

NOTES: dict[str, str] = {}
READ_ONLY = os.environ.get("FAKE_READONLY") == "1"
TOOLS = [
    {"name": "read_note", "description": "Read a note by key.", "annotations": {"readOnlyHint": True},
     "inputSchema": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}},
    {"name": "whoami", "description": "Report the server's environment token and working directory.",
     "annotations": {"readOnlyHint": True}, "inputSchema": {"type": "object", "properties": {}}},
]
if not READ_ONLY:
    TOOLS += [
        {"name": "write_note", "description": "Write a note.", "annotations": {"readOnlyHint": False},
         "inputSchema": {"type": "object", "properties": {"key": {"type": "string"}, "text": {"type": "string"}},
                         "required": ["key", "text"]}},
        {"name": "delete_all", "description": "Delete every note.",
         "annotations": {"readOnlyHint": False, "destructiveHint": True},
         "inputSchema": {"type": "object", "properties": {}}},
    ]


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def text(t):
    return {"content": [{"type": "text", "text": t}]}


for line in sys.stdin:
    msg = json.loads(line)
    method, mid = msg.get("method"), msg.get("id")
    if method is None:
        continue                                          # a response to our roots/list request
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": "srv-1", "method": "roots/list", "params": {}})
        send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                                                      "serverInfo": {"name": "fake", "version": "1"}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        name, args = msg["params"]["name"], msg["params"].get("arguments") or {}
        if name == "read_note":
            out = text(NOTES.get(args["key"], ""))
        elif name == "whoami":
            out = text(json.dumps({"token": os.environ.get("FAKE_TOKEN"), "cwd": os.getcwd(),
                                   "extra": os.environ.get("ARBITER_GATEWAY_LEAK")}))
        elif name == "write_note":
            NOTES[args["key"]] = args["text"]
            out = text("ok")
        elif name == "delete_all":
            NOTES.clear()
            out = text("deleted")
        else:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool {name}"}})
            continue
        send({"jsonrpc": "2.0", "id": mid, "result": out})
    elif mid is not None:
        send({"jsonrpc": "2.0", "id": mid, "result": {}})
