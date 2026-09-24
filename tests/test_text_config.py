"""JSONC and block-YAML minimal-edit writers: idempotent, comment-preserving, reversible."""

from pathlib import Path

import pytest

from arbiter_agent.clients import text_config as tc
from arbiter_agent.clients.config_merge import ConfigConflict

P = Path("x")
ENTRY = {"command": "C:\\arbiter\\arbiter.exe", "args": ["mcp"]}
GOOSE = {"type": "stdio", "cmd": "C:\\arbiter\\arbiter.exe", "args": ["mcp"], "enabled": True, "name": "arbiter",
         "timeout": 300}


def ours(e):
    return isinstance(e, dict) and "arbiter" in str(e).lower()


def prune(d):
    if isinstance(d, dict):
        out = {k: prune(v) for k, v in d.items()}
        return {k: v for k, v in out.items() if v != {}}
    return d


JSONC = {
    "empty": b"",
    "plain": b'{\n  "theme": "dark"\n}\n',
    "comments": b'// Zed settings\n{\n  // font\n  "buffer_font_size": 15, /* inline */\n  "theme": "One Dark",\n}\n',
    "existing_container": b'{\n  "context_servers": {\n    // mine\n    "other": {"command": "x"}\n  }\n}\n',
    "empty_container": b'{\n  "mcp": {}\n}',
    "tabs": b'{\n\t"a": 1,\n\t"mcp": {\n\t\t"servers": {\n\t\t\t"x": {"command": "y"}\n\t\t}\n\t}\n}\n',
}


@pytest.mark.parametrize("case", sorted(JSONC))
@pytest.mark.parametrize("container", [["context_servers"], ["mcp", "servers"]])
def test_jsonc_roundtrip(case, container):
    raw = JSONC[case]
    new = tc.jsonc_upsert(raw or None, P, container, "arbiter", ENTRY, ours)
    assert tc.jsonc_upsert(new, P, container, "arbiter", ENTRY, ours) == new       # idempotent
    box = tc.load_jsonc(new, P)
    for k in container:
        box = box[k]
    assert box["arbiter"] == ENTRY
    back = tc.jsonc_remove(new, P, container, "arbiter", ours)
    if raw:
        assert prune(tc.load_jsonc(back, P)) == prune(tc.load_jsonc(raw, P))
        for comment in (b"// Zed settings", b"/* inline */", b"// mine", b"// font"):
            if comment in raw:
                assert comment in new and comment in back


def test_jsonc_refuses_foreign_entry():
    raw = b'{"mcpServers": {"arbiter": {"command": "something-else"}}}'
    with pytest.raises(ConfigConflict):
        tc.jsonc_upsert(raw, P, ["mcpServers"], "arbiter", ENTRY, ours)


YAML = {
    "empty": b"",
    "no_extensions": b"GOOSE_PROVIDER: openai\nGOOSE_MODEL: gpt-5  # comment\n",
    "extensions": (b"# goose config\nGOOSE_PROVIDER: anthropic\nextensions:\n  developer:\n    enabled: true\n"
                   b"    name: developer\n    type: builtin\nother: 1\n"),
    "extensions_4": b"extensions:\n    developer:\n        enabled: true\n        type: builtin\n",
    "extensions_null": b"extensions:\nother: 2\n",
}


@pytest.mark.parametrize("case", sorted(YAML))
def test_yaml_roundtrip(case):
    raw = YAML[case]
    new = tc.yaml_upsert(raw or None, P, "extensions", "arbiter", GOOSE, ours)
    assert tc.yaml_upsert(new, P, "extensions", "arbiter", GOOSE, ours) == new
    assert tc._yaml_load(new, P)["extensions"]["arbiter"] == GOOSE
    back = tc.yaml_remove(new, P, "extensions", "arbiter", ours)
    if raw and case != "extensions_null":
        assert back == raw
    if b"# goose config" in raw:
        assert b"# goose config" in new


def test_yaml_refuses_flow_style_container():
    with pytest.raises(ConfigConflict):
        tc.yaml_upsert(b"extensions: {developer: {type: builtin}}\n", P, "extensions", "arbiter", GOOSE, ours)
