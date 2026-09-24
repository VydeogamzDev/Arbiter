import time

import pytest

from arbiter_agent.audit.replay import Reducer, replay
from arbiter_agent.clients.ingest import Ingestor
from arbiter_agent.privacy.retention import RetentionPolicy, run_retention
from arbiter_agent.privacy.scope import ProjectScope
from arbiter_agent.state.store import connect, migrate
from arbiter_agent.state.writer import Writer


@pytest.fixture
def ing(home):
    migrate(home.db, home.backups)
    w = Writer(home.db).start()
    red = Reducer()
    i = Ingestor(key=b"k" * 32, writer=w, reducer=red, scope=ProjectScope(home.scope_file), max_payload_bytes=4096)
    yield i
    w.stop()


def hook(event, **kw):
    return {"hook_event_name": event, "session_id": "s1", "turn_id": "t1", "cwd": "C:/proj", **kw}


def rows(home, sql="SELECT * FROM event_log ORDER BY seq"):
    c = connect(home.db, readonly=True)
    try:
        return [dict(r) for r in c.execute(sql)]
    finally:
        c.close()


def test_idempotent_redelivery_dropped(ing, home):
    p = hook("Stop", stop_hook_active=False, last_assistant_message="Done.")
    assert ing.ingest_hook("codex", p, surface="mcp").status == "stored"
    assert ing.ingest_hook("codex", dict(p), surface="mcp").status == "redelivered"
    assert len(rows(home)) == 1


def test_cross_surface_duplicate_kept_as_evidence(ing, home):
    post = hook("PostToolUse", tool_use_id="call_1", tool_name="Bash", tool_response="3 passed")
    r1 = ing.ingest_hook("codex", post, surface="mcp")
    r2 = ing.ingest_transcript_record("codex", {"id": "r9", "exit_code": 0}, session_id="s1", record_id="r9",
                                      record_type="tool_result", tool_use_id="call_1")
    assert r1.status == "stored" and r2.status == "duplicate" and r2.duplicate_of == r1.seq
    evs = rows(home)
    assert evs[1]["duplicate_of"] == evs[0]["seq"] and evs[1]["surface"] == "transcript"
    st = ing.reducer.sessions["codex:s1"]
    assert st.events == 1 and st.duplicates == 1


def test_pending_tool_reconciliation_and_out_of_order(ing):
    ing.ingest_hook("codex", hook("PreToolUse", tool_use_id="a", tool_name="Bash"), surface="mcp")
    ing.ingest_hook("codex", hook("PreToolUse", tool_use_id="b", tool_name="Bash"), surface="mcp")
    ing.ingest_hook("codex", hook("PostToolUse", tool_use_id="a", tool_name="Bash"), surface="mcp")
    # result for c arrives before its pre_tool (out of order)
    ing.ingest_hook("codex", hook("PostToolUse", tool_use_id="c", tool_name="Bash"), surface="mcp")
    ing.ingest_hook("codex", hook("PreToolUse", tool_use_id="c", tool_name="Bash"), surface="mcp")
    ing.ingest_hook("codex", {"hook_event_name": "SessionEnd", "session_id": "s1", "cwd": "C:/proj"}, surface="mcp")
    st = ing.reducer.sessions["codex:s1"]
    assert st.resolved_tools == 1 and st.orphan_results == 1
    assert sorted(st.pending_tools) == ["b"] and st.unresolved_at_end == ["b"] and st.ended


def test_replay_reproduces_live_reducer(ing, home):
    for i in range(20):
        ing.ingest_hook("codex", hook("PreToolUse", tool_use_id=f"x{i}", tool_name="Bash"), surface="mcp")
        if i % 3:
            ing.ingest_hook("codex", hook("PostToolUse", tool_use_id=f"x{i}", tool_name="Bash"), surface="mcp")
    ing.ingest_hook("claude_code", {"hook_event_name": "Stop", "session_id": "c9", "prompt_id": "p1",
                                    "stop_hook_active": True, "cwd": "C:/proj"}, surface="http")
    ing.ingest_transcript_record("codex", {"id": "q"}, session_id="s1", record_id="q", record_type="tool_result",
                                 tool_use_id="x1")
    ing.writer.run(lambda c: None)
    c = connect(home.db, readonly=True)
    try:
        assert replay(c).snapshot() == ing.reducer.snapshot()
    finally:
        c.close()


def test_oversized_payload_truncated_with_hash(ing, home):
    big = hook("PostToolUse", tool_use_id="big", tool_name="Bash", tool_response="x" * 20000)
    ing.ingest_hook("codex", big, surface="mcp")
    blob = rows(home, "SELECT truncated, full_size, size FROM blob")[0]
    ev = rows(home)[0]
    assert blob["truncated"] == 1 and blob["full_size"] > 20000 and blob["size"] <= 4096
    assert ev["sensitivity_class"] == "truncated"


def test_scope_exclusion_counts_without_storing(ing, home, tmp_path):
    proj = tmp_path / "private"
    proj.mkdir()
    (proj / ".arbiterignore").write_text("")
    r = ing.ingest_hook("codex", hook("UserPromptSubmit", prompt="top secret plans", cwd=str(proj)), surface="mcp")
    ing.writer.run(lambda c: None)
    assert r.status == "excluded"
    assert rows(home) == [] and rows(home, "SELECT * FROM blob") == []
    assert rows(home, "SELECT reason, count FROM scope_skip") == [{"reason": "arbiterignore", "count": 1}]


def test_retention_expires_closed_sessions_only(ing, home):
    ing.ingest_hook("codex", hook("UserPromptSubmit", prompt="old work"), surface="mcp")
    ing.ingest_hook("codex", {"hook_event_name": "SessionEnd", "session_id": "s1", "cwd": "C:/proj"}, surface="mcp")
    active = {"hook_event_name": "UserPromptSubmit", "session_id": "live", "turn_id": "t", "cwd": "C:/proj",
              "prompt": "current work"}
    ing.ingest_hook("codex", active, surface="mcp")
    future = time.time() + 40 * 86400  # 40 days later: s1 blobs expired, rows kept (180d)
    ing.writer.run(lambda c: c.execute("UPDATE client_session SET last_event_at = ? WHERE id = 'codex:live'",
                                       (future - 60,)))
    rep = ing.writer.run(lambda c: run_retention(c, home.db, RetentionPolicy(), now=future))
    assert rep.blobs_deleted == 2 and rep.rows_deleted == 0
    live_ptr = rows(home, "SELECT payload_pointer FROM event_log WHERE session_id = 'codex:live'")[0]["payload_pointer"]
    assert rows(home, f"SELECT hash FROM blob WHERE hash = '{live_ptr}'")  # active session untouched
    rep = ing.writer.run(lambda c: run_retention(c, home.db, RetentionPolicy(), now=time.time() + 200 * 86400))
    assert rep.rows_deleted == 2  # s1 rows past 180d
    assert {r["session_id"] for r in rows(home)} == {"codex:live"}


def test_storage_cap_never_touches_active_sessions(ing, home):
    ing.ingest_hook("codex", hook("UserPromptSubmit", prompt="a" * 3000), surface="mcp")
    ing.ingest_hook("codex", {"hook_event_name": "SessionEnd", "session_id": "s1", "cwd": "C:/proj"}, surface="mcp")
    ing.ingest_hook("codex", {"hook_event_name": "UserPromptSubmit", "session_id": "live", "turn_id": "t",
                              "cwd": "C:/proj", "prompt": "b" * 3000}, surface="mcp")
    rep = ing.writer.run(lambda c: run_retention(c, home.db, RetentionPolicy(storage_cap_bytes=1)))
    assert rep.cap_blobs_deleted >= 1
    live_ptr = rows(home, "SELECT payload_pointer FROM event_log WHERE session_id = 'codex:live'")[0]["payload_pointer"]
    assert rows(home, f"SELECT hash FROM blob WHERE hash = '{live_ptr}'")


def test_repeatable_hook_events_dedupe_only_within_window(ing, home):
    """Clients without turn ids (Claude Code) repeat identical prompts/stops legitimately."""
    import time as _t

    p = {"hook_event_name": "UserPromptSubmit", "session_id": "rw", "prompt": "continue", "cwd": str(home.data)}
    assert ing.ingest_hook("claude_code", dict(p), surface="http").status == "stored"
    assert ing.ingest_hook("claude_code", dict(p), surface="http").status == "redelivered"
    _t.sleep(2.1)
    assert ing.ingest_hook("claude_code", dict(p), surface="http").status == "stored"
