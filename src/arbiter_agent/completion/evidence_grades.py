"""Contract evaluation against evidence (spec §6.4.1, §12.3).

A contract becomes PASS only when its recipe's evidence rule is met by evidence of a sufficient
origin and an accepted grade. ``agent_asserted`` evidence never satisfies a contract alone.
Test evidence must be *fresh*: a run observed before the latest code change is stale.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from arbiter_agent.state.contract_compiler import expand_paths, public_surface
from arbiter_agent.state.facts import is_doc_path
from arbiter_agent.state.schema import ORIGIN_RANK, Contract
from arbiter_agent.telemetry.diff_stats import changed_since
from arbiter_agent.telemetry.runner_parsers.base import tokens

SAFE_FLAGS = re.compile(
    r"^(?:-q+|-v+|-s|-x|-rA|-r\w+|--quiet|--verbose|--tb=\w+|--color=\w+|--colou?r|--no-header|--no-summary|--ci|"
    r"--silent|--reporter=[\w-]+|--no-coverage|--no-cov|--runinband|-i|--count=1|-count=1|-race|--nocapture|--|"
    r"--ff|--failed-first|-n|auto|\d+|--durations=\d+|--cache-clear|--maxfail=\d+|--exitfirst|-p=no:cacheprovider|"
    r"--disable-warnings|-w|--watch=false|--run|--passwithnotests=false|--no-watch|--forceexit|--detectopenhandles|"
    r"--release|--all-features|--workspace|--locked|-v=\w+|--verbosity=\w+|--nologo|--no-build|--no-restore|"
    r"--pretty|--show-error-codes|--strict|--no-incremental)$", re.I)
NARROWING = re.compile(r"^(?:-k|--deselect|-m|--lf|--last-failed|--sw|--stepwise|-t|--testnamepattern|--grep|-g|"
                       r"-run|--filter|--only|--skip|--ignore|--exclude|--changed|--onlychanged|-o|--bail)", re.I)
SUBCOMMANDS = {"run", "test", "exec", "check", "nextest", "vitest", "jest", "pytest", "mocha", "go", "cargo", "dotnet",
               "npm", "pnpm", "yarn", "bun", "make", "tox", "nox", "tsc", "eslint", "ruff", "mypy", "unittest"}


@dataclass
class ContractEval:
    status: str                        # pass | fail | unknown | waived
    grade: str | None = None           # direct | indirect | unavailable | conflicted
    reason: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)
    origin: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvalContext:
    facts: list[dict[str, Any]]
    root: str | None
    baseline_head: str | None = None
    baseline_dirty: bool | None = None
    decisions: dict[str, str] = field(default_factory=dict)       # contract short id -> waive|confirm|reject
    accept_grades: tuple[str, ...] = ("direct", "indirect")

    def last_code_change_seq(self) -> int:
        seqs = [f["source_seq"] for f in self.facts if f["kind"] == "file_change"
                and not is_doc_path(str(f.get("subject") or "")) and self._in_repo(str(f.get("subject") or ""))]
        return max(seqs) if seqs else 0

    def _in_repo(self, subject: str) -> bool:
        """Scratch files outside the repository (an agent's own temp scripts) can't make the repo's test
        results stale; seen as a false block in a Sonnet 5 benchmark run. Relative and <shell> subjects
        stay conservative."""
        if not self.root or not subject or subject.startswith("<"):
            return True
        p = Path(subject)
        if not p.is_absolute():
            return True
        try:
            p.resolve().relative_to(Path(self.root).resolve())
            return True
        except (ValueError, OSError):
            return False


def _pathish(t: str) -> bool:
    return "/" in t or "\\" in t or "::" in t or t.startswith(".") or bool(re.search(r"\.\w{1,5}$", t))


def _under(child: str, parent: str) -> bool:
    c, p = child.replace("\\", "/").strip("./"), parent.replace("\\", "/").strip("./")
    if p in ("", "...") or parent in ("./...", "..."):
        return True
    p = p.removesuffix("/...")
    return c == p or c.startswith(p + "/") or c.startswith(p + "::")


def command_match(recipe_cmd: str, fact_cmd: str) -> str | None:
    """'exact' | 'superset' | None. A narrower or filtered run never matches."""
    r, f = tokens(recipe_cmd), tokens(fact_cmd)
    if not r or not f or r[0] != f[0]:
        return None
    rest_f = list(f)
    for t in r:
        if t in rest_f:
            rest_f.remove(t)
    missing = [t for t in r if t not in f]
    if any(NARROWING.match(t) for t in rest_f):
        return None
    if not missing:
        return "exact" if all(SAFE_FLAGS.match(t) for t in rest_f) else None
    # broader run: every missing recipe token must be a path covered by one of the run's paths (or a whole-suite run)
    f_paths = [t for t in f[1:] if _pathish(t) and not t.startswith("-")]
    extra_ok = all(SAFE_FLAGS.match(t) or (_pathish(t) and not t.startswith("-")) or t in SUBCOMMANDS for t in rest_f)
    if not extra_ok:
        return None
    for t in missing:
        if t.startswith("-"):
            if SAFE_FLAGS.match(t):
                continue
            return None
        if not _pathish(t):
            return None
        if f_paths and not any(_under(t, p) for p in f_paths):
            return None
    return "superset"


def _ev(fact: dict[str, Any], note: str = "") -> dict[str, Any]:
    d = {"fact": fact["id"], "kind": fact["kind"], "origin": fact["origin"], "status": fact["status"],
         "subject": fact.get("subject")}
    if note:
        d["note"] = note
    return d


def _eval_test(c: Contract, ctx: EvalContext) -> ContractEval:
    cmd = str(c.recipe.params["command"])
    cands = []
    for f in ctx.facts:
        if f["kind"] != "test_run" or ORIGIN_RANK.get(f["origin"], -1) < ORIGIN_RANK["host_reported"]:
            continue
        m = command_match(cmd, str(f["data"].get("command") or f.get("subject") or ""))
        if m:
            cands.append((f, m))
    if not cands:
        runs = [f for f in ctx.facts if f["kind"] == "command_run" and command_match(cmd, str(f["data"].get("command")
                                                                                              or ""))]
        if runs and runs[-1]["data"].get("unavailable"):
            return ContractEval("unknown", "unavailable", "the test command isn't available here", [_ev(runs[-1])])
        if runs:
            return ContractEval("unknown", None, "the command ran but its output wasn't a recognized test result",
                                [_ev(runs[-1])])
        return ContractEval("unknown", None, "no matching test run observed")
    change = ctx.last_code_change_seq()
    fresh = [(f, m) for f, m in cands if f["source_seq"] >= change]
    if not fresh:
        f, _ = cands[-1]
        return ContractEval("unknown", None, "stale: files changed after the last matching run", [_ev(f, "stale")])
    f, m = fresh[-1]
    if f["data"].get("unavailable"):
        return ContractEval("unknown", "unavailable", "the test command isn't available here", [_ev(f)])
    statuses = {x["status"] for x, _ in fresh}
    if f["status"] == "fail":
        return ContractEval("fail", "direct" if m == "exact" else "indirect", "latest matching run failed", [_ev(f)],
                            f["origin"])
    if f["status"] != "pass":
        return ContractEval("unknown", None, f["data"].get("note") or "latest matching run has no clear result",
                            [_ev(f)])
    if "fail" in statuses:
        return ContractEval("unknown", "conflicted", "matching runs since the last change disagree (flaky?)",
                            [_ev(x) for x, _ in fresh[-3:]])
    grade = "direct" if m == "exact" else "indirect"
    if grade not in ctx.accept_grades:
        return ContractEval("unknown", grade, f"{grade} evidence isn't accepted for this recipe", [_ev(f)])
    return ContractEval("pass", grade, "matching run passed" + (" (broader run)" if m == "superset" else ""),
                        [_ev(f)], f["origin"])


def _eval_exit(c: Contract, ctx: EvalContext) -> ContractEval:
    cmd = str(c.recipe.params["command"])
    want = int(c.recipe.params.get("exit_code", 0))
    cands = [f for f in ctx.facts if f["kind"] in ("test_run", "command_run")
             and ORIGIN_RANK.get(f["origin"], -1) >= ORIGIN_RANK["host_reported"]
             and command_match(cmd, str(f["data"].get("command") or "")) == "exact"]
    if not cands:
        return ContractEval("unknown", None, "command not observed")
    f = cands[-1]
    if f["source_seq"] < ctx.last_code_change_seq():
        return ContractEval("unknown", None, "stale: files changed after the last run", [_ev(f, "stale")])
    code = f["data"].get("exit_code")
    if isinstance(code, int):
        ok = code == want
        return ContractEval("pass" if ok else "fail", "direct", f"exit status {code}", [_ev(f)], f["origin"])
    is_err = f["data"].get("is_error")
    if is_err is None:
        return ContractEval("unknown", None, "exit status not observed yet", [_ev(f)])
    if want == 0:
        # Claude Code reports success/failure (is_error), not the number: success means exit 0.
        return ContractEval("fail" if is_err else "pass", "direct", "failed" if is_err else "succeeded", [_ev(f)],
                            f["origin"])
    if not is_err:
        return ContractEval("fail", "direct", f"succeeded, expected exit {want}", [_ev(f)], f["origin"])
    return ContractEval("unknown", None, "failed, but the exact exit code isn't reported", [_ev(f)])


def _resolve(root: str | None, path: str) -> Path | None:
    p = Path(path)
    if p.is_absolute():
        return p
    return Path(root) / p if root else None


def _eval_file(c: Contract, ctx: EvalContext) -> ContractEval:
    p = _resolve(ctx.root, str(c.recipe.params["path"]))
    if p is None:
        return ContractEval("unknown", "unavailable", "repository root unknown")
    absent = bool(c.recipe.params.get("absent"))
    obs = {"observed": "file", "path": str(c.recipe.params["path"]), "origin": "arbiter_observed"}
    if c.recipe.type == "file_exists":
        ok = p.is_file() != absent
        return ContractEval("pass" if ok else "fail", "direct", "exists" if p.is_file() else "missing", [obs],
                            "arbiter_observed")
    if not p.is_file():
        return ContractEval("fail", "direct", "file missing", [obs], "arbiter_observed")
    try:
        text = p.read_text("utf-8", errors="replace")
    except OSError as exc:
        return ContractEval("unknown", "unavailable", f"unreadable: {exc}", [obs])
    if c.recipe.params.get("regex"):
        try:
            found = re.search(str(c.recipe.params["regex"]), text, re.M) is not None
        except re.error:
            return ContractEval("unknown", None, "invalid regex in recipe")
    else:
        found = str(c.recipe.params.get("text", "")) in text
    ok = found != absent
    return ContractEval("pass" if ok else "fail", "direct", "content check " + ("met" if ok else "not met"), [obs],
                        "arbiter_observed")


def _sha(p: Path) -> str | None:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return None


def _eval_unchanged(c: Contract, ctx: EvalContext) -> ContractEval:
    if not ctx.root:
        return ContractEval("unknown", "unavailable", "repository root unknown")
    snap = (c.snapshot or {}).get("files")
    if snap is None:
        return ContractEval("unknown", None, "no snapshot recorded for these paths")
    changed: list[str] = []
    for key, before in snap.items():
        p = _resolve(ctx.root, key)
        now = (_sha(p) if c.recipe.type == "paths_unchanged" else public_surface(p)) if p else None
        if now != before:
            changed.append(key)
    current = {str(f.relative_to(ctx.root)).replace("\\", "/") for f in expand_paths(ctx.root, c.recipe.params["paths"])
               if str(f).startswith(ctx.root)}
    changed += sorted(k for k in current - set(snap) if c.recipe.type == "paths_unchanged")
    # Changes made before the contract existed count too, when the baseline tree was clean.
    if c.recipe.type == "paths_unchanged" and ctx.baseline_head and ctx.baseline_dirty is False:
        since = changed_since(ctx.root, ctx.baseline_head, c.recipe.params["paths"]) or []
        changed += [n for n in since if n not in changed]
    obs = {"observed": "hash" if c.recipe.type == "paths_unchanged" else "surface", "origin": "arbiter_observed",
           "changed": changed[:20]}
    if changed:
        return ContractEval("fail", "direct", f"changed: {', '.join(changed[:5])}", [obs], "arbiter_observed")
    return ContractEval("pass", "direct", "unchanged", [obs], "arbiter_observed")


def evaluate(c: Contract, ctx: EvalContext) -> ContractEval:
    d = ctx.decisions.get(c.id)
    if d == "waive":
        return ContractEval("waived", None, "waived by the user", [{"decision": "waive", "origin": "user"}], "user")
    if c.recipe.type == "manual":
        if d == "confirm":
            return ContractEval("pass", "direct", "confirmed by the user", [{"decision": "confirm", "origin": "user"}],
                                "user")
        return ContractEval("unknown", None, "needs your confirmation (`arbiter contracts confirm`)")
    if c.recipe.type == "test_command":
        return _eval_test(c, ctx)
    if c.recipe.type == "command_exit":
        return _eval_exit(c, ctx)
    if c.recipe.type in ("file_exists", "file_contains"):
        return _eval_file(c, ctx)
    if c.recipe.type in ("paths_unchanged", "public_surface_unchanged"):
        return _eval_unchanged(c, ctx)
    return ContractEval("unknown", None, f"unsupported recipe {c.recipe.type}")
