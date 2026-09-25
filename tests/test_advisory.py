"""M9: advisory modules: diff risk (shadow), retrieval reranker, reasoning scheduler, Host Advisory
API v0 (shadow), session signals and the audit trail."""

from __future__ import annotations

import json
import subprocess
import time
from typing import Any

import pytest

from arbiter_agent.daemon.client import DaemonClient, DaemonError
from arbiter_agent.eval import diff_gate, retrieval_gate
from arbiter_agent.eval.trace_replay import Harness
from arbiter_agent.host import api as host_api
from arbiter_agent.reasoning import scheduler
from arbiter_agent.reasoning.leases import LeaseBook
from arbiter_agent.retrieval import candidates, reranker
from arbiter_agent.retrieval.miss_detector import MissDetector
from arbiter_agent.review import review_policy, test_mapper, test_scheduler
from arbiter_agent.review.diff_risk import FileChange, classify, parse_unified_diff


# ------------------------------------------------------------------ diff risk (M9.3)
def test_parse_and_classify_basics():
    d = ("diff --git a/app/auth/login.py b/app/auth/login.py\n--- a/app/auth/login.py\n+++ b/app/auth/login.py\n"
         "@@ -1,2 +1 @@\n-    if not check_password(u, p):\n-        raise Unauthorized()\n+    pass\n"
         "diff --git a/docs/a.md b/docs/a.md\nnew file mode 100644\n--- /dev/null\n+++ b/docs/a.md\n"
         "@@ -0,0 +1 @@\n+hi\n")
    changes = parse_unified_diff(d)
    assert [c.path for c in changes] == ["app/auth/login.py", "docs/a.md"] and changes[1].status == "A"
    auth = classify(changes[0])
    assert auth.level == "critical" and "security check" in auth.reasons[0]
    assert classify(changes[1]).level == "low"
    assert classify(FileChange("x/api.py", added=["    return eval(expr)"])).level == "high"
    assert classify(FileChange("src/util.py", added=["# a comment"])).level == "low"
    key = classify(FileChange("app/settings/prod.py", added=["STRIPE_KEY = 'sk_live_abcdefghijklmnop1234'"]))
    assert key.level == "high" and any("hard-coded credential" in r for r in key.reasons)


def test_blast_radius_raises_level_and_maps_tests():
    rev = {"core/parse.py": {f"mod{i}.py" for i in range(9)} | {"tests/test_parse.py"}}
    files = ["core/parse.py", "tests/test_parse.py"] + [f"mod{i}.py" for i in range(9)]
    rep = review_policy.analyze([FileChange("core/parse.py", added=["    return x + 1"])], files=files, rev=rev)
    f = rep.files[0]
    assert f["level"] == "high" and any("blast radius" in r for r in f["reasons"])
    assert f["tests"] == ["tests/test_parse.py"] and rep.test_order[0] == "tests/test_parse.py"
    assert rep.allocation["review"] == "explicit high-effort review"


def test_test_order_puts_recent_failures_first():
    risks = [classify(FileChange("a.py", added=["x = 1"])), classify(FileChange("auth/b.py", added=["token = 1"]))]
    mapped = test_mapper.map_tests(["a.py", "auth/b.py"], ["tests/test_a.py", "tests/test_b.py"],
                                   {"a.py": {"tests/test_a.py"}, "auth/b.py": {"tests/test_b.py"}})
    order = test_scheduler.order(mapped, risks, recently_failed=["tests/test_a.py"])
    assert order[0] == "tests/test_a.py" and order[1] == "tests/test_b.py"


def test_low_risk_sampling_is_stratified_and_reproducible():
    risks = [classify(FileChange(p, added=["# c"])) for p in ("a/x.py", "a/y.py", "b/z.md", "b/w.md", "b/v.md")]
    s1 = review_policy.sample_low_risk(risks, 0.2, "salt")
    assert s1 == review_policy.sample_low_risk(risks, 0.2, "salt")
    assert {p.split("/")[0] for p in s1} == {"a", "b"}          # at least one per stratum


def test_diff_gate_development_sets():
    corpus = diff_gate.CORPUS.parent
    for f in ("diffs.yaml", "diffs_dev2.yaml", "diffs_dev3.yaml", "diffs_dev4.yaml"):
        rep = diff_gate.run(corpus / f)
        assert rep["passed"] and not rep["dangerous_misses"], (f, rep["dangerous_misses"])


# ------------------------------------------------------------------ retrieval (M9.2)
def test_trace_refs_and_adaptive_k():
    files = ["shop/a.py", "web/src/b.ts"]
    errs = 'File "shop/a.py", line 3, in f\n    at g (web/src/b.ts:9:2)'
    assert candidates.trace_refs(errs, files) == [("shop/a.py", 3), ("web/src/b.ts", 9)]
    flat = [(f"f{i}", 1.0, ["lexical"]) for i in range(12)]
    peaked = [("f0", 10.0, ["lexical"])] + [(f"f{i}", 0.01, ["lexical"]) for i in range(1, 12)]
    base = candidates.QueryContext("q")
    assert reranker.adaptive_k(flat, base, 0)[0] > reranker.adaptive_k(peaked, base, 0)[0]
    missed = candidates.QueryContext("q", misses=2)
    assert reranker.adaptive_k(flat, missed, 0)[0] > reranker.adaptive_k(flat, base, 0)[0]
    tight = candidates.QueryContext("q", budget_tokens=2400)
    assert reranker.adaptive_k(flat, tight, 0)[0] == reranker.MIN_K


@pytest.mark.parametrize("queries", ["queries.yaml", "heldout_shop_v1.yaml", "heldout_notes_v1.yaml"])
def test_retrieval_recall_gate(queries):
    rep = retrieval_gate.run(queries=queries)
    assert rep["recall"] >= retrieval_gate.MIN_RECALL, [d for d in rep["detail"] if d["recall"] < 1]


def test_miss_detector():
    md = MissDetector()
    md.edited("s", "a.py")                       # nothing surfaced yet: not a miss
    assert not md.signal("s")["retrieval_miss"]
    md.surfaced("s", ["b.py"], "fix the parser bug")
    md.edited("s", "c.py")
    assert md.signal("s")["retrieval_miss"]
    md2 = MissDetector()
    for _ in range(3):
        md2.surfaced("t", ["x.py"], "parser bug in tokenize")
    assert any("repeated search" in r for r in md2.recent("t"))


# ------------------------------------------------------------------ reasoning scheduler (M9.1)
ALLOWED = [
    {"model": "luna", "tier": 1, "efforts": ["low", "medium", "high"],
     "price": {"input": 0.25, "cached_input": 0.025, "output": 2.0}},
    {"model": "terra", "tier": 2, "efforts": ["low", "medium", "high"],
     "price": {"input": 1.25, "cached_input": 0.125, "output": 10.0}},
    {"model": "sol", "tier": 3, "efforts": ["medium", "high", "xhigh"],
     "price": {"input": 5.0, "cached_input": 0.5, "output": 40.0}},
]


def _allowed_keys(allowed: list[dict[str, Any]]) -> set[tuple[str, str]]:
    return {(a["model"], e) for a in allowed for e in a["efforts"]}


def test_recommendation_stays_in_allowed_set_and_meets_floor():
    easy = scheduler.recommend({"task_tier": "low", "phase": "format"}, ALLOWED)
    hard = scheduler.recommend({"task_tier": "critical", "phase": "debug", "failures": 2}, ALLOWED)
    for r in (easy, hard):
        assert not r.abstain and (r.model, r.effort) in _allowed_keys(ALLOWED)
    assert easy.model == "luna"
    assert hard.model == "sol" and hard.floor == 3
    # capability is relative to the host's set (absolute tier floors are the host's job, §4.6.1): a high
    # floor picks the strongest options offered, never anything below the floor within the set
    weak = [{"model": "luna", "tier": 1, "efforts": ["low"]}, {"model": "mini", "tier": 0, "efforts": ["low"]}]
    r = scheduler.recommend({"task_tier": "critical"}, weak)
    assert not r.abstain and r.model == "luna"
    assert all(not s["eligible"] for s in r.scores if s["model"] == "mini")
    assert scheduler.recommend({}, []).abstain and scheduler.recommend({}, [{"tier": 1}]).abstain


def test_retrieval_miss_does_not_escalate_but_loop_does():
    base = scheduler.wanted_level({"task_tier": "medium", "failures": 2})[0]
    miss_lvl, _, advice = scheduler.wanted_level({"task_tier": "medium", "failures": 2, "retrieval_miss": True})
    loop_lvl, _, loop_advice = scheduler.wanted_level({"task_tier": "medium", "loop": True})
    assert miss_lvl < base and any("broaden retrieval" in a for a in advice)
    assert loop_lvl == 3 and any("replan" in a for a in loop_advice)


def test_cache_warmth_and_priors_shift_the_choice():
    ctx = {"task_tier": "medium", "expected_input_tokens": 200_000, "expected_output_tokens": 500}
    cold = scheduler.recommend(ctx, ALLOWED)
    warm = scheduler.recommend({**ctx, "cache_warm": {"terra": 1.0}}, ALLOWED)
    assert warm.model == "terra"
    assert cold.model != "sol"
    failing = [{"model": warm.model, "success": False}] * 6
    after = scheduler.recommend({**ctx, "cache_warm": {"terra": 1.0}, "prior_outcomes": failing}, ALLOWED)
    assert after.model != "terra"


def test_lease_hysteresis():
    lb = LeaseBook(high_lease_calls=2, expensive_lease_calls=1, deescalate_checkpoints=2)
    assert lb.apply("t", 3, False)[0] == 3
    assert lb.apply("t", 1, True)[0] == 3            # lease holds
    assert lb.apply("t", 1, True)[0] == 3            # lease holds (last call)
    assert lb.apply("t", 1, False)[0] == 3           # no progress: hold
    assert lb.apply("t", 1, True)[0] == 3            # one checkpoint isn't enough
    assert lb.apply("t", 1, True)[0] == 2            # two checkpoints: down one level only


# ------------------------------------------------------------------ Host Advisory API (shadow)
def _host(h: Harness) -> host_api.HostAPI:
    return host_api.HostAPI(h.writer, h.db, config=h.config, engine=h.engine)


def _rows(h: Harness) -> list[Any]:
    from arbiter_agent.state.store import connect

    h.writer.run(lambda c: None)
    rc = connect(h.db, readonly=True)
    try:
        return rc.execute("SELECT * FROM advisory_decision ORDER BY created_at").fetchall()
    finally:
        rc.close()


def test_host_recommend_logs_and_outcomes_are_project_confined():
    with Harness() as h:
        api = _host(h)
        ctx = {"task_tier": "medium", "task_id": "T1", "expected_input_tokens": 200_000, "cache_warm": {"terra": 1.0}}
        a = api.recommend_call({"call_context": ctx, "allowed_set": ALLOWED, "project": {"id": "alpha"}}, "hivemind")
        assert a["mode"] == "shadow" and a["model"] == "terra" and a["decision_id"].startswith("d_")
        for _ in range(6):
            r = api.recommend_call({"call_context": ctx, "allowed_set": ALLOWED, "project": {"id": "alpha"}},
                                   "hivemind")
            api.report_outcome({"decision_id": r["decision_id"], "outcome": {"success": False, "junk": 1}},
                               "hivemind")
        h.writer.run(lambda c: None)
        alpha = api.recommend_call({"call_context": ctx, "allowed_set": ALLOWED, "project": {"id": "alpha"}},
                                   "hivemind")
        beta = api.recommend_call({"call_context": ctx, "allowed_set": ALLOWED, "project": {"id": "beta"}},
                                  "hivemind")
        assert alpha["model"] != "terra"          # alpha's failures moved alpha's recommendation
        assert beta["model"] == "terra"           # ... and never beta's (project confinement)
        rows = _rows(h)
        assert len(rows) == 9 and {r["partition_key"] for r in rows} == {
            host_api.partition_key({"id": "alpha"}), host_api.partition_key({"id": "beta"})}
        stored = [json.loads(r["outcome_json"]) for r in rows if r["outcome_json"]]
        assert stored and all("junk" not in o for o in stored)
        with pytest.raises(LookupError):
            api.report_outcome({"decision_id": a["decision_id"], "outcome": {}}, "someone-else")


def test_host_abstains_on_deadline_and_outside_allowed(monkeypatch):
    with Harness() as h:
        api = _host(h)
        slow = api.recommend_call({"call_context": {}, "allowed_set": ALLOWED, "deadline_ms": 0.0001}, "h")
        assert slow["abstain"] and "deadline" in slow["reason"]
        monkeypatch.setattr(scheduler, "recommend", lambda *a, **k: scheduler.Recommendation(
            "gpt-imaginary", "max", 4, 0, False, "ok"))
        bad = api.recommend_call({"call_context": {}, "allowed_set": ALLOWED}, "h")
        assert bad["abstain"] and bad["model"] is None and "outside the allowed set" in bad["reason"]
        assert api.capabilities()["mode"] == "shadow"


# ------------------------------------------------------------------ engine signals, advice, diff risk
def _git(repo: Any, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_engine_signals_advice_and_diff_risk():
    with Harness() as h:
        _git(h.repo, "init", "-q")
        _git(h.repo, "config", "user.email", "t@example.com")
        _git(h.repo, "config", "user.name", "t")
        h.write("app/auth/login.py", "def login(u, p):\n    if not check_password(u, p):\n        raise Denied()\n")
        _git(h.repo, "add", ".")
        _git(h.repo, "commit", "-qm", "init")
        h.prompt("Fix the login bug.")
        h.baseline()
        h.edit("app/auth/login.py", "def login(u, p):\n    return True\n")
        for _ in range(3):
            h.shell("pytest", "1 failed in 0.01s", exit_code=1)
        h.drain()
        sig = h.engine.signals(h.sid)
        assert sig["no_progress"] and not sig["verified_since_last_change"]
        adv = h.engine.advice(h.sid)
        assert adv["diff_risk"]["level"] == "critical"
        assert adv["reasoning"]["level"] >= 3            # critical diff risk floor + no progress
        h.engine.misses.surfaced(h.sid, ["README.md"], "login bug")
        h.edit("app/other.py", "x = 1\n")
        h.drain()
        assert h.engine.signals(h.sid)["retrieval_miss"]


# ------------------------------------------------------------------ daemon round trip
def test_daemon_host_api_and_audit_trail(daemon):
    home, _ = daemon
    with DaemonClient(home, component="hivemind") as c:
        cap = c.request("host.capabilities", {})
        assert cap["api_version"] == host_api.API_VERSION and cap["mode"] == "shadow"
        rec = c.request("host.recommend_call", {"call_context": {"task_tier": "high"}, "allowed_set": ALLOWED,
                                                "project": {"id": "p1"}, "deadline_ms": 2000})
        assert not rec["abstain"] and (rec["model"], rec["effort"]) in _allowed_keys(ALLOWED)
        ack = c.request("host.report_outcome", {"decision_id": rec["decision_id"],
                                                "outcome": {"success": True, "latency_ms": 900}})
        assert ack["ack"]
        with pytest.raises(DaemonError):
            c.request("host.nope", {})
    time.sleep(0.3)
    with DaemonClient(home, component="cli") as u:
        hist = u.request("advice_list", {"limit": 5})["decisions"]
    assert hist and hist[0]["kind"] == "recommend_call" and hist[0]["host"] == "hivemind"
    assert hist[0]["outcome"] == {"success": True, "latency_ms": 900} and hist[0]["reasons"]
