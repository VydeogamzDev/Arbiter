"""Runner parsers and detection."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass

from arbiter_agent.telemetry.runner_parsers.base import RunnerResult, fingerprint

ParseFn = Callable[[str], RunnerResult | None]


def _count(label: str, text: str) -> int:
    m = re.findall(rf"(\d+)\s+{label}\b", text, re.I)
    return int(m[-1]) if m else 0


def parse_pytest(out: str) -> RunnerResult | None:
    lines = [ln for ln in out.splitlines()
             if re.search(r"\b(passed|failed|error|errors|skipped|deselected|xfailed|no tests ran)\b", ln)
             and re.search(r"\bin [\d.]+s\b|=====", ln)]
    if not lines:
        return None
    s = lines[-1]
    passed, failed = _count("passed", s), _count("failed", s)
    errors = _count("errors?", s)
    skipped = _count("skipped", s) + _count("deselected", s)
    if "no tests ran" in s.lower():
        return RunnerResult("pytest", "test", "unknown", total=0, note="no tests ran")
    status = "fail" if failed or errors else ("pass" if passed else "unknown")
    return RunnerResult("pytest", "test", status, passed, failed, errors, skipped, passed + failed + errors)


def parse_unittest(out: str) -> RunnerResult | None:
    m = re.search(r"^Ran (\d+) tests? in [\d.]+s", out, re.M)
    if not m:
        return None
    total = int(m.group(1))
    tail = out[m.end():]
    if re.search(r"^FAILED \(", tail, re.M):
        f = _kv("failures", tail)
        e = _kv("errors", tail)
        return RunnerResult("unittest", "test", "fail", total - f - e, f, e, _kv("skipped", tail), total)
    if re.search(r"^OK\b", tail, re.M):
        if total == 0:
            return RunnerResult("unittest", "test", "unknown", total=0, note="no tests ran")
        return RunnerResult("unittest", "test", "pass", total - _kv("skipped", tail), 0, 0, _kv("skipped", tail),
                            total)
    return None


def _kv(key: str, text: str) -> int:
    m = re.search(rf"\b{key}=(\d+)", text)
    return int(m.group(1)) if m else 0


def parse_jest(out: str) -> RunnerResult | None:
    m = re.findall(r"^Tests:\s+(.+total)", out, re.M)
    if not m:
        return None
    s = m[-1]
    failed, passed, skipped, total = (_count("failed", s), _count("passed", s),
                                      _count("skipped", s) + _count("todo", s), _count("total", s))
    status = "fail" if failed else ("pass" if passed else "unknown")
    return RunnerResult("jest", "test", status, passed, failed, 0, skipped, total)


def parse_vitest(out: str) -> RunnerResult | None:
    m = re.findall(r"^\s*Tests\s+(.+\(\d+\))", out, re.M)
    if not m:
        return None
    s = m[-1]
    failed, passed, skipped = _count("failed", s), _count("passed", s), _count("skipped", s)
    total_m = re.search(r"\((\d+)\)", s)
    status = "fail" if failed else ("pass" if passed else "unknown")
    total = int(total_m.group(1)) if total_m else None
    return RunnerResult("vitest", "test", status, passed, failed, 0, skipped, total)


def parse_mocha(out: str) -> RunnerResult | None:
    p = re.search(r"^\s*(\d+) passing\b", out, re.M)
    f = re.search(r"^\s*(\d+) failing\b", out, re.M)
    if not p and not f:
        return None
    passed, failed = int(p.group(1)) if p else 0, int(f.group(1)) if f else 0
    pend = re.search(r"^\s*(\d+) pending\b", out, re.M)
    status = "fail" if failed else ("pass" if passed else "unknown")
    return RunnerResult("mocha", "test", status, passed, failed, 0, int(pend.group(1)) if pend else 0, passed + failed)


def parse_go(out: str) -> RunnerResult | None:
    ok_pk = len(re.findall(r"^ok\s+\S+", out, re.M))
    fail_pk = len(re.findall(r"^FAIL\s+\S+", out, re.M))
    fails = len(re.findall(r"^\s*--- FAIL:", out, re.M))
    passes = len(re.findall(r"^\s*--- PASS:", out, re.M))
    if not (ok_pk or fail_pk or fails or re.search(r"^(PASS|FAIL)$", out, re.M)):
        return None
    if fail_pk or fails or re.search(r"^FAIL$", out, re.M):
        return RunnerResult("go", "test", "fail", passes, max(fails, fail_pk), note=f"{fail_pk} failing packages")
    if ok_pk or re.search(r"^PASS$", out, re.M):
        return RunnerResult("go", "test", "pass", passes or ok_pk, 0, note=f"{ok_pk} packages ok")
    return None


def parse_cargo(out: str) -> RunnerResult | None:
    results = re.findall(r"test result: (ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored", out)
    if not results:
        return None
    passed = sum(int(r[1]) for r in results)
    failed = sum(int(r[2]) for r in results)
    ignored = sum(int(r[3]) for r in results)
    status = "fail" if failed or any(r[0] == "FAILED" for r in results) else ("pass" if passed else "unknown")
    return RunnerResult("cargo", "test", status, passed, failed, 0, ignored, passed + failed + ignored)


def parse_dotnet(out: str) -> RunnerResult | None:
    m = re.findall(r"(Passed|Failed)!\s+-\s+Failed:\s+(\d+),\s+Passed:\s+(\d+),\s+Skipped:\s+(\d+),\s+Total:\s+(\d+)",
                   out)
    if not m:
        return None
    failed = sum(int(x[1]) for x in m)
    passed = sum(int(x[2]) for x in m)
    status = "fail" if failed or any(x[0] == "Failed" for x in m) else ("pass" if passed else "unknown")
    skipped, total = sum(int(x[3]) for x in m), sum(int(x[4]) for x in m)
    return RunnerResult("dotnet", "test", status, passed, failed, 0, skipped, total)


def parse_tsc(out: str) -> RunnerResult | None:
    errs = re.findall(r"error TS\d+:", out)
    m = re.search(r"Found (\d+) errors?", out)
    if errs or m:
        n = int(m.group(1)) if m else len(errs)
        return RunnerResult("tsc", "typecheck", "fail" if n else "pass", errors=n)
    return None  # silence alone is not a pass without a known exit code


def parse_eslint(out: str) -> RunnerResult | None:
    m = re.search(r"✖ (\d+) problems? \((\d+) errors?, (\d+) warnings?\)", out)
    if not m:
        return None
    errors = int(m.group(2))
    return RunnerResult("eslint", "lint", "fail" if errors else "pass", errors=errors, note=f"{m.group(3)} warnings")


def parse_ruff(out: str) -> RunnerResult | None:
    if re.search(r"^All checks passed!", out, re.M):
        return RunnerResult("ruff", "lint", "pass")
    m = re.search(r"^Found (\d+) errors?", out, re.M)
    return RunnerResult("ruff", "lint", "fail", errors=int(m.group(1))) if m else None


def parse_mypy(out: str) -> RunnerResult | None:
    if re.search(r"^Success: no issues found", out, re.M):
        return RunnerResult("mypy", "typecheck", "pass")
    m = re.search(r"^Found (\d+) errors? in \d+ files?", out, re.M)
    return RunnerResult("mypy", "typecheck", "fail", errors=int(m.group(1))) if m else None


@dataclass(frozen=True)
class RunnerParser:
    name: str
    version: int
    command: re.Pattern[str]
    parse: ParseFn
    shape_fallback: bool = False   # try on generic commands (npm test, make test) by output shape


PARSERS: list[RunnerParser] = [
    RunnerParser("pytest", 1, re.compile(r"\b(py\.?test)\b"), parse_pytest, True),
    RunnerParser("unittest", 1, re.compile(r"\bunittest\b|\bmanage\.py test\b"), parse_unittest, True),
    RunnerParser("jest", 1, re.compile(r"\bjest\b"), parse_jest, True),
    RunnerParser("vitest", 1, re.compile(r"\bvitest\b"), parse_vitest, True),
    RunnerParser("mocha", 1, re.compile(r"\bmocha\b"), parse_mocha, True),
    RunnerParser("go", 1, re.compile(r"\bgo test\b"), parse_go, False),
    RunnerParser("cargo", 1, re.compile(r"\bcargo (test|nextest)\b"), parse_cargo, True),
    RunnerParser("dotnet", 1, re.compile(r"\bdotnet test\b"), parse_dotnet, True),
    RunnerParser("tsc", 1, re.compile(r"\btsc\b"), parse_tsc, False),
    RunnerParser("eslint", 1, re.compile(r"\beslint\b"), parse_eslint, False),
    RunnerParser("ruff", 1, re.compile(r"\bruff\b"), parse_ruff, False),
    RunnerParser("mypy", 1, re.compile(r"\bmypy\b"), parse_mypy, False),
]
GENERIC_TEST = re.compile(r"\b(npm|pnpm|yarn|bun)\s+(run\s+)?test\b|\bmake\s+(test|check)\b|\btox\b|\bnox\b|"
                          r"\bjust\s+test\b|\bgradle\w*\s+test\b|\bmvn\w*\s+test\b")


def detect(command: str, output: str, exit_code: int | None = None) -> RunnerResult | None:
    """Recognize and parse a runner result. ``None`` means "not a recognized runner output"."""
    fp = fingerprint(command)
    result: RunnerResult | None = None
    for p in PARSERS:
        if p.command.search(fp):
            result = p.parse(output)
            if result is not None:
                result.parser_version = p.version
                break
    if result is None and GENERIC_TEST.search(fp):
        for p in PARSERS:
            if p.shape_fallback:
                result = p.parse(output)
                if result is not None:
                    result.parser_version = p.version
                    break
    if result is None:
        return None
    if exit_code is not None:
        if result.status == "pass" and exit_code != 0:
            result.status, result.note = "unknown", f"parsed pass contradicts exit code {exit_code}"
        elif result.status == "unknown" and exit_code != 0 and (result.failed or result.errors):
            result.status = "fail"
    return result


def junit_xml(text: str) -> RunnerResult | None:
    """JUnit/xUnit XML report (pytest --junitxml, jest-junit, surefire...)."""
    try:
        root = ET.fromstring(text)  # noqa: S314 - local report file, not network input
    except ET.ParseError:
        return None
    suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
    if not suites:
        return None
    tests = failures = errors = skipped = 0
    for s in suites:
        tests += int(s.get("tests", 0) or 0)
        failures += int(s.get("failures", 0) or 0)
        errors += int(s.get("errors", 0) or 0)
        skipped += int(s.get("skipped", 0) or s.get("disabled", 0) or 0)
    if tests == 0:
        return RunnerResult("junit", "test", "unknown", total=0, note="no tests in report")
    status = "fail" if failures or errors else "pass"
    return RunnerResult("junit", "test", status, tests - failures - errors - skipped, failures, errors, skipped, tests)
