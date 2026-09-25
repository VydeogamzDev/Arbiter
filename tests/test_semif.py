"""M7: the semantic sensor service (spec §7): validation, budgeting, mirroring, reliability,
backends (mocked), the shadow harness and the benchmark. No model weights needed."""

from __future__ import annotations

import json
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from arbiter_agent.concurrency import Deadline
from arbiter_agent.config.loader import build_config
from arbiter_agent.eval.trace_replay import Harness
from arbiter_agent.semif import benchmark, hardware, mirroring
from arbiter_agent.semif.backends import NullBackend, ScoreRequest, from_config
from arbiter_agent.semif.backends.encoder import EncoderBackend
from arbiter_agent.semif.backends.llama_cpp import LlamaCppBackend
from arbiter_agent.semif.request_budget import BudgetError, fit
from arbiter_agent.semif.service import SemIfService
from arbiter_agent.semif.shadow import ShadowHarness, question
from arbiter_agent.semif.types import TEMPLATE_VERSION, Question, ScoreResult, StateSection
from arbiter_agent.semif.validation import validate
from arbiter_agent.state.store import connect


def _q(family: str = "completion_claim", text: str = "All done, the tests pass.", **kw: Any) -> Question:
    return question(family, [StateSection("final assistant message", text, 0, pinned=True)]) if not kw else \
        Question(family, "Is it done?", ["yes", "no"], [StateSection("m", text, 0, True)], binary_positive="yes", **kw)


def _result(options: list[str], probs: list[float], state_hash: str, **kw: Any) -> ScoreResult:
    base: dict[str, Any] = dict(model="fake", revision="r1", backend="fake", precision="fp32",
                                template_version=TEMPLATE_VERSION, tokenizer="t", state_hash=state_hash,
                                criterion_hash="c", latency_ms=1.0)
    base.update(kw)
    return ScoreResult(list(options), list(probs), **base)


class FakeBackend:
    """Scores the positive option with ``p`` (or ``fn(request)``), whatever the option order."""
    name = "fake"
    model = "fake-model"
    revision = "r1"
    precision = "fp32"
    tokenizer = "bytes"

    def __init__(self, p: float = 0.9, fn: Any = None, max_tokens: int = 100_000, raise_exc: bool = False,
                 block: threading.Event | None = None, mangle: Any = None) -> None:
        self.p, self.fn, self.max_tokens, self.raise_exc = p, fn, max_tokens, raise_exc
        self.block, self.mangle = block, mangle
        self.calls = 0

    def count_tokens(self, text: str) -> int | None:
        return None

    def score(self, requests: list[ScoreRequest], deadline: Deadline) -> list[ScoreResult]:
        self.calls += 1
        if self.block is not None:
            self.block.wait(10)
        if self.raise_exc:
            raise ConnectionError("backend down")
        out = []
        for r in requests:
            p = self.fn(r) if self.fn else self.p
            probs = [p if o in ("yes", "new_task") else 1 - p for o in r.options]
            res = ScoreResult(list(r.options), probs, self.model, self.revision, self.name, self.precision,
                              TEMPLATE_VERSION, self.tokenizer, r.state_hash, r.criterion_hash, 0.5)
            out.append(self.mangle(res) if self.mangle else res)
        return out

    def health(self) -> dict[str, Any]:
        return {"backend": self.name, "ok": True}

    def close(self) -> None:
        pass


# ------------------------------------------------------------------ validation
def test_validation_rejects_every_malformed_shape():
    q = _q()
    sh = q.state_hash
    good = _result(["yes", "no"], [0.8, 0.2], sh)
    assert validate(good, q, ["yes", "no"], sh) == (True, "")
    bad = {
        "order": (_result(["no", "yes"], [0.8, 0.2], sh), "order"),
        "len": (_result(["yes", "no"], [1.0], sh), "number"),
        "nan": (_result(["yes", "no"], [math.nan, 0.5], sh), "non-finite"),
        "inf": (_result(["yes", "no"], [math.inf, 0.0], sh), "non-finite"),
        "range": (_result(["yes", "no"], [1.5, -0.5], sh), "range"),
        "sum": (_result(["yes", "no"], [0.5, 0.2], sh), "sum"),
        "template": (_result(["yes", "no"], [0.8, 0.2], sh, template_version="q0"), "template"),
        "unnamed": (_result(["yes", "no"], [0.8, 0.2], sh, revision=""), "revision"),
        "hash": (_result(["yes", "no"], [0.8, 0.2], "other"), "state hash"),
    }
    for name, (r, why) in bad.items():
        ok, reason = validate(r, q, ["yes", "no"], sh)
        assert not ok and why in reason, name
    ok, reason = validate(good, q, ["yes", "no"], sh, expected_revision="r2")
    assert not ok and "revision" in reason


# ------------------------------------------------------------------ budgeting
def test_budget_keeps_pinned_drops_low_priority_and_rejects_impossible():
    q = Question("completion_claim", "Is it done?", ["yes", "no"], [
        StateSection("request", "Fix the bug in calc.py", 0, pinned=True),
        StateSection("history", "x" * 2000, 90),
        StateSection("tool state", "pytest: 3 passed", 10, pinned=True),
    ], binary_positive="yes")
    f = fit(q, 600)
    assert f.dropped == ["history"] and "Fix the bug" in f.state_text and "3 passed" in f.state_text
    assert f.state_text.index("request") < f.state_text.index("tool state")      # original order kept
    assert not f.exact and f.tokens <= 600
    assert fit(q, 100_000).dropped == []
    with pytest.raises(BudgetError, match="question alone"):
        fit(q, 20)
    big = Question("x", "Is it done?", ["yes", "no"], [StateSection("req", "y" * 5000, 0, pinned=True)])
    with pytest.raises(BudgetError, match="pinned"):
        fit(big, 1000)
    # an exact tokenizer is used when offered
    assert fit(q, 100_000, counter=lambda s: len(s.split())).exact


# ------------------------------------------------------------------ mirroring
def test_mirroring_variants_and_aggregate():
    q = _q()
    assert mirroring.variants(q) == [(["yes", "no"], None), (["no", "yes"], None)]
    hi = Question("c", "Done?", ["yes", "no"], impact="high", paraphrase="Finished?", binary_positive="yes")
    assert len(mirroring.variants(hi)) == 3
    sh = q.state_hash
    agree = mirroring.aggregate(q, [(["yes", "no"], _result(["yes", "no"], [0.9, 0.1], sh)),
                                    (["no", "yes"], _result(["no", "yes"], [0.12, 0.88], sh))])
    assert agree.choice == "yes" and not agree.abstain and agree.spread == pytest.approx(0.02)
    # position bias: the model always picks option A -> variants disagree -> abstain
    biased = mirroring.aggregate(q, [(["yes", "no"], _result(["yes", "no"], [0.9, 0.1], sh)),
                                     (["no", "yes"], _result(["no", "yes"], [0.9, 0.1], sh))])
    assert biased.abstain and biased.choice is None and "disagree" in biased.reason
    thin = mirroring.aggregate(q, [(["yes", "no"], _result(["yes", "no"], [0.55, 0.45], sh))])
    assert thin.abstain and "margin" in thin.reason
    hard = mirroring.aggregate(q, [(["yes", "no"], _result(["yes", "no"], [1.0, 0.0], sh, hard_label=True))])
    assert hard.abstain and "hard labels" in hard.reason
    assert hard.score_kind == "model_score"          # never "probability" before calibration


# ------------------------------------------------------------------ service
def test_null_backend_abstains_and_default_config_is_null():
    svc = SemIfService.from_config(build_config({}))
    assert isinstance(svc.decoder, NullBackend) and isinstance(svc.encoder, NullBackend)
    j = svc.judge(_q())
    assert j.abstain and j.choice is None and "no semantic score" in j.reason


def test_service_judges_with_mirrors():
    be = FakeBackend(0.9)
    svc = SemIfService(be)
    j = svc.judge(_q())
    assert j.choice == "yes" and not j.abstain and j.variants == 2 and svc.stats["invalid"] == 0


@pytest.mark.parametrize("mangle", [
    lambda r: ScoreResult(**{**r.to_dict(), "probs": [math.nan, 0.5]}),
    lambda r: ScoreResult(**{**r.to_dict(), "probs": [0.7, 0.7]}),
    lambda r: ScoreResult(**{**r.to_dict(), "options": list(reversed(r.options))}),
    lambda r: ScoreResult(**{**r.to_dict(), "state_hash": "stale"}),
    lambda r: ScoreResult(**{**r.to_dict(), "template_version": "zz"}),
])
def test_malformed_results_are_never_consumed(mangle):
    svc = SemIfService(FakeBackend(0.9, mangle=mangle))
    j = svc.judge(_q())
    assert j.abstain and j.choice is None and "invalid result" in j.reason
    assert svc.stats["invalid"] == 2


def test_revision_pin_rejects_other_models():
    svc = SemIfService(FakeBackend(0.9), expected_revision="r2")
    j = svc.judge(_q())
    assert j.abstain and "revision" in j.reason


def test_backend_errors_back_off_and_breaker_trips():
    be = FakeBackend(raise_exc=True)
    svc = SemIfService(be)
    j = svc.judge(_q())
    assert j.abstain and "backend error" in j.reason
    j2 = svc.judge(_q())
    assert "backing off" in j2.reason and be.calls == 1          # back-off: no second call
    svc._backoff_until.clear()
    for _ in range(5):
        svc.judge(_q())
        svc._backoff_until.clear()
    assert "circuit breaker open" in svc.judge(_q()).reason
    assert svc.health()["breakers"]["completion_claim"]["open"] if "open" in \
        svc.health()["breakers"]["completion_claim"] else True


def test_budget_rejection_before_model_work():
    be = FakeBackend(max_tokens=30)
    svc = SemIfService(be)
    j = svc.judge(_q())
    assert j.abstain and j.reason.startswith("budget") and be.calls == 0 and svc.stats["budget_rejected"] == 1


def test_queue_sheds_and_never_blocks():
    gate = threading.Event()
    svc = SemIfService(FakeBackend(0.9, block=gate), queue_capacity=2).start()
    try:
        t0 = time.perf_counter()
        futs = [svc.submit(_q()) for _ in range(20)]
        assert time.perf_counter() - t0 < 0.5
        assert any(f is None for f in futs) and svc.stats["shed"] >= 1
    finally:
        gate.set()
        svc.stop()


def test_encoder_routing_and_fake_model():
    class Model:
        def classify_text(self, text: str, schema: dict[str, Any], include_confidence: bool = False) -> Any:
            labels = schema["answer"]["labels"]
            return {"answer": {"label": "yes" if "done" in text.lower() else "no", "confidence": 0.9}} \
                if "yes" in labels else {}

    enc = EncoderBackend(model=Model(), revision="enc-r1")
    svc = SemIfService(FakeBackend(0.1), enc, encoder_families=["completion_claim"])
    assert svc.backend_for("completion_claim") is enc and svc.backend_for("scope_change").name == "fake"
    j = svc.judge(_q(text="All done."))
    assert j.choice == "yes" and j.results[0].backend == "encoder"
    assert svc.judge(_q(text="Still working on it.")).choice == "no"

    class HardModel:
        def classify_text(self, text: str, schema: dict[str, Any]) -> Any:
            return {"answer": "yes"}

    j = SemIfService(None, EncoderBackend(model=HardModel(), revision="x"),
                     encoder_families=["completion_claim"]).judge(_q())
    assert j.abstain and "hard labels" in j.reason


# ------------------------------------------------------------------ llama.cpp (mock server)
class _Server:
    def __init__(self, mode: str = "logprobs") -> None:
        self.mode = mode
        self.bodies: list[dict[str, Any]] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:
                pass

            def _send(self, obj: Any) -> None:
                data = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                if self.path == "/props":
                    props = {"model_path": "C:\\models\\Qwen3.5-4B-Q4_K_M.gguf", "build_info": "b9999",
                             "default_generation_settings": {"n_ctx": 8192}}
                    if outer.mode == "chat":
                        props["chat_template"] = "{{ messages }}"
                    self._send(props)
                else:
                    self._send({"status": "ok"})

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                outer.bodies.append(body)
                if self.path == "/tokenize":
                    self._send({"tokens": list(range(len(body["content"].split())))})
                    return
                if self.path == "/apply-template":
                    assert body["chat_template_kwargs"] == {"enable_thinking": False}
                    self._send({"prompt": "<|im_start|>user\n" + body["messages"][0]["content"] +
                                          "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"})
                    return
                if outer.mode == "chat" and "<think>\n\n</think>" not in body["prompt"]:
                    first = {"top_logprobs": [{"token": "<think>", "logprob": 0.0}]}   # would start reasoning
                    self._send({"completion_probabilities": [first]})
                    return
                # the answer letter for "yes" is wherever "yes" appears in the prompt's option list
                yes_first = body["prompt"].find("A. yes") != -1 or body["prompt"].find("A) yes") != -1
                a, b = (0.85, 0.1) if yes_first else (0.1, 0.85)
                if outer.mode == "offtopic":
                    a, b = 0.05, 0.05
                if outer.mode == "probs":
                    first = {"probs": [{"tok_str": "A", "prob": a}, {"tok_str": " B", "prob": b},
                                       {"tok_str": "The", "prob": 0.05}]}
                elif outer.mode == "top_probs":
                    first = {"top_probs": [{"token": "A", "prob": a}, {"token": "B", "prob": b}]}
                else:
                    first = {"top_logprobs": [{"token": "A", "logprob": math.log(a)},
                                              {"token": "B", "logprob": math.log(b)}]}
                self._send({"completion_probabilities": [first], "timings": {"cache_n": 12}})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.mark.parametrize("mode", ["logprobs", "probs", "top_probs", "chat"])
def test_llama_cpp_backend_scores_letters(mode):
    srv = _Server(mode)
    try:
        be = LlamaCppBackend(srv.url, revision="qwen-r1")
        assert be.model == "Qwen3.5-4B-Q4_K_M.gguf" and be.count_tokens("a b c") == 3
        assert be.chat == (mode == "chat") and be.max_tokens == (8192 - 64 if mode == "chat" else 8192)
        svc = SemIfService(be)
        j = svc.judge(_q(), deadline_s=10)
        assert j.choice == "yes" and not j.abstain, j.reason
        assert all(r.cache_mode == "prefix_cached" for r in j.results)
        comp = [b for b in srv.bodies if "n_predict" in b]
        assert comp and all(b["n_predict"] == 1 and b["cache_prompt"] for b in comp)
        assert not any(b.get("post_sampling_probs") for b in comp)       # pre-sampling scores only
        if mode == "chat":
            assert all("</think>" in b["prompt"] for b in comp)          # reasoning disabled via the template
        assert be.health()["ok"]
    finally:
        srv.close()


def test_llama_cpp_off_topic_answer_abstains_and_loopback_only():
    srv = _Server("offtopic")
    try:
        j = SemIfService(LlamaCppBackend(srv.url, revision="r")).judge(_q(), deadline_s=10)
        assert j.abstain and "option letter" in j.reason
    finally:
        srv.close()
    with pytest.raises(ValueError, match="loopback"):
        LlamaCppBackend("http://10.0.0.5:8088")
    # nothing listening: from_config degrades to the null backend instead of failing
    cfg = build_config({"semif": {"enabled": True, "llama_cpp_url": "http://127.0.0.1:9"}})
    be = from_config(cfg, "decoder")
    assert isinstance(be, NullBackend) and "unavailable" in be.reason


# ------------------------------------------------------------------ shadow harness (engine integration)
def _sensor_rows(h: Harness) -> list[Any]:
    rc = connect(h.db, readonly=True)
    try:
        return rc.execute("SELECT * FROM sensor_log ORDER BY id").fetchall()
    finally:
        rc.close()


def test_shadow_logs_beside_rules_and_never_changes_decisions():
    def run(with_sensor: bool) -> tuple[dict[str, Any], list[Any], list[dict[str, Any]]]:
        with Harness() as h:
            svc = SemIfService(FakeBackend(0.95)).start()
            if with_sensor:
                h.engine.decision_listeners.append(ShadowHarness(svc, h.writer).on_decision)
            h.prompt("Fix the addition bug in calc.py.")
            h.prompt("New task: write a README.")
            h.stop("All done, the tests pass.")
            h.drain()
            svc.queue.drain(10)
            svc.stop()
            st = h.engine.session_status(h.sid, light=True)
            return {"epoch": st["goal_epoch"], "counts": st["counts"]}, _sensor_rows(h), list(h.responses)

    base, rows0, resp0 = run(False)
    shadow, rows, resp = run(True)
    assert shadow == base and resp == resp0 and rows0 == []
    fams = [r["family"] for r in rows]
    assert fams.count("scope_change") == 1 and fams.count("completion_claim") == 1   # first prompt: not asked
    new_task = next(r for r in rows if r["family"] == "scope_change")
    assert new_task["rule_decision"] == "confirm_new" and new_task["choice"] == "new_task"
    assert all(r["backend"] == "fake" and r["model"] == "fake-model" for r in rows)
    assert json.loads(rows[0]["judgment_json"])["score_kind"] == "model_score"


def test_hook_latency_does_not_depend_on_a_slow_sensor():
    gate = threading.Event()
    with Harness() as h:
        svc = SemIfService(FakeBackend(0.9, block=gate)).start()
        shadow = ShadowHarness(svc, h.writer)
        h.engine.decision_listeners.append(shadow.on_decision)
        try:
            t0 = time.perf_counter()
            for i in range(5):
                h.prompt(f"step {i}: fix the thing")
            h.drain()
            assert time.perf_counter() - t0 < 5.0 and not gate.is_set()   # backend still blocked
            assert shadow.stats["queued"] + shadow.stats["shed"] == 4       # prompts 2..5
        finally:
            gate.set()
            svc.stop()


def test_listener_errors_are_contained():
    with Harness() as h:
        def boom(*a: Any) -> None:
            raise RuntimeError("listener bug")
        h.engine.decision_listeners.append(boom)
        h.prompt("Fix the addition bug in calc.py.")
        h.drain()
        assert h.engine.session_status(h.sid, light=True)["goal_epoch"] == 1


# ------------------------------------------------------------------ benchmark + tiers
def test_benchmark_null_and_oracle():
    null = benchmark.run(SemIfService())
    fams = null["families"]
    assert set(fams) == {"completion_claim", "scope_change", "requirement_detection"}
    assert all(m["coverage"] == 0.0 and m["items"] > 0 and m["rules_balanced_accuracy"] is not None
               for m in fams.values())
    labels = {question(it["family"], it["sections"]).state_text(): it["label"] for it in benchmark.items()}
    oracle = SemIfService(FakeBackend(fn=lambda r: 0.95 if labels[r.state_text] else 0.05))
    rep = benchmark.run(oracle)
    for fam, m in rep["families"].items():
        assert m["coverage"] == 1.0 and m["balanced_accuracy"] == 1.0, fam
        assert m["brier"] < 0.01 and m["ece"] < 0.1
    assert "completion_claim" in benchmark.render(rep)


def test_tier_plan_by_vram():
    cfg = build_config({})
    none = hardware.plan(cfg, gpus=[])
    assert none.decoder is None and any("no NVIDIA GPU" in n for n in none.notes)
    small = hardware.plan(cfg, gpus=[hardware.Gpu("RTX 3050", 4.0)])
    assert small.decoder is None
    mid = hardware.plan(cfg, gpus=[hardware.Gpu("RTX 3080 Ti", 12.0)])
    assert mid.decoder and mid.decoder["model"] == "K2-Horizon-7B" and mid.decoder["quant"] == "Q4_K_M"
    eight = hardware.plan(cfg, gpus=[hardware.Gpu("RTX 4060", 8.0)])
    assert eight.decoder and eight.decoder["model"] == "JevK5"


def test_encoder_loads_local_folder_quietly(tmp_path, monkeypatch, capsys):
    import sys
    import types

    folder = tmp_path / "GLiNER2.5-Decide"
    folder.mkdir()
    (folder / "config.json").write_text('{"model": "x"}', encoding="utf-8")
    (folder / "model.safetensors").write_bytes(b"\0" * 1234)
    seen = {}

    class Auto:
        @staticmethod
        def from_pretrained(path, **kw):
            seen["path"] = path
            print("\U0001f9e0 Model Configuration")      # gliner2's banner: crashes a cp1252 console
            return types.SimpleNamespace(classify_text=lambda *a, **k: {"answer": {"label": "yes",
                                                                                    "confidence": 0.9}})

    monkeypatch.setitem(sys.modules, "gliner2", types.SimpleNamespace(AutoExtractor=Auto))
    be = EncoderBackend(str(folder), device="cpu")
    assert seen["path"] == str(folder) and capsys.readouterr().out == ""
    assert be.model == "GLiNER2.5-Decide" and be.revision.startswith("local:") and be.revision.endswith(":1234")
    (folder / "config.json").write_text('{"model": "y"}', encoding="utf-8")
    assert EncoderBackend(str(folder)).revision != be.revision       # a changed checkpoint changes the revision
