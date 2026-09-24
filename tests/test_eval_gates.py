"""M3/M4 exit gates (spec 20.17) over the packaged corpus. Runs in CI."""

from arbiter_agent.eval import gates


def test_all_eval_gates_pass():
    report = gates.run_all()
    failed = [g for g in report["gates"] if not g["passed"]]
    assert not failed, failed
    assert report["trace_cases_ok"], report["details"]["gate"]["failed_cases"]
    d = report["details"]
    assert d["parsers"]["cases"] >= 50 and d["gate"]["cases"] >= 30
    assert d["integrity"]["weakening_cases"] >= 20 and d["contracts"]["requirements"] >= 40
