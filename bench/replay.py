"""Replay analysis of recorded agent sessions: how much of the remaining cost could new Arbiter
mechanisms remove? (2026-09-28)

For every read or search an agent made, decide whether Arbiter could have supplied the answer ahead
of time, from information it had at that moment:

  own_edit     re-reading a file the agent itself edited earlier in the session
  test_of      reading a test file whose source the session already edited or was shown
  pack_partial reading a file the pack listed, outlined or excerpted (but didn't show in full)
  prev_output  reading a file named in an earlier tool output (e.g. a search hit)
  prompt       reading a file, or searching for a name, that the current prompt mentions
  subsumed     re-reading content already read, with no edit to the file since (pure waste)

A model request whose every call is a predictable lookup is "removable": had Arbiter attached the
answers to the previous result, the model wouldn't have needed that request. Its recorded cost
(uncached input + 0.1 x cached + 8 x output) is the estimated saving; the attached content itself is
charged as uncached input at its size.

Sources: Pi events (bench runs/<run>/<cond>/<task>/rep*/events.jsonl) and Codex session rollouts
(the benchmark Codex home), matched to runs by workspace name.

    python -m bench.replay
"""

from __future__ import annotations

import glob
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

RUNS = Path("D:/ArbiterBench/runs")
CODEX_SESSIONS = Path("D:/ArbiterBench/codex-home/sessions")
SEARCH = re.compile(r"(?:^|[\s;|&(])(?:rg|grep|findstr|Select-String|find|ls|dir|Get-ChildItem|"
                    r"git\s+(?:grep|ls-files))\b")
READ = re.compile(r"(?:^|[\s;|&(])(?:Get-Content|cat|type|sed\s+-n|head|tail|more)\s+(?:-\S+\s+)*"
                  r"[\"']?([\w./\\-]+\.\w+)")
TEST = re.compile(r"pytest|vitest|jest|npm\s+test|go\s+test")
PATHLIKE = re.compile(r"[\w./\\-]+\.(?:py|ts|tsx|js|jsx|mjs|go|rs|md|json)\b")
IDENT = re.compile(r"`([A-Za-z_$][\w$.]*)")


def norm(p: str) -> str:
    p = p.replace("\\", "/")
    m = re.search(r"/ws/[^/]+/(.*)$", p)
    return (m.group(1) if m else p).lstrip("./")


def is_test_path(p: str) -> bool:
    n = p.rsplit("/", 1)[-1]
    return "/tests/" in p or n.startswith("test_") or n.split(".")[0] in ("test", "spec") or ".test." in n


def source_of(test: str) -> list[str]:
    """Plausible sources of a test file (sympy: pkg/tests/test_x.py -> pkg/x.py; date-fns: X/test.ts -> X/index.ts)."""
    d, n = (test.rsplit("/", 1) + [""])[:2] if "/" in test else ("", test)
    out = []
    if n.startswith("test_"):
        out.append(f"{d.removesuffix('/tests')}/{n[5:]}")
    if n.split(".")[0] in ("test", "spec"):
        out.append(f"{d}/index.{n.rsplit('.', 1)[-1]}")
    return out


@dataclass
class Call:
    kind: str                 # read | search | edit | test | other
    target: str = ""          # path (read/edit) or query text (search)
    out_tokens: int = 0       # size of the tool's result


@dataclass
class Request:
    calls: list[Call]
    cost: float
    prompt_idx: int
    ctx: int = 0              # tokens sent with this request (cached or not)


@dataclass
class Session:
    agent: str
    repo: str
    task: str
    rep: int
    prompts: list[str] = field(default_factory=list)
    packs: list[tuple[int, dict[str, str]]] = field(default_factory=list)   # (prompt idx, path -> how shown)
    requests: list[Request] = field(default_factory=list)


def pack_files(text: str) -> dict[str, str]:
    shown = {}
    for path, how in re.findall(r"\n--- (\S+)(?: \((\w+))?", text):
        shown[path] = how or "full"
    m = re.search(r"likely relevant: ([^\n]+)", text)
    if m:
        for p in m.group(1).split(", "):
            shown.setdefault(p.strip(), "listed")
    return shown


def classify_bash(cmd: str) -> list[Call]:
    calls = []
    for part in re.split(r";|&&|\n", cmd):
        part = part.strip()
        if not part:
            continue
        if TEST.search(part):
            calls.append(Call("test", part[:80]))
        elif (m := READ.search(part)):
            calls.append(Call("read", norm(m.group(1))))
        elif SEARCH.search(part):
            calls.append(Call("search", part[:160]))
        else:
            calls.append(Call("other", part[:80]))
    return calls or [Call("other", cmd[:80])]


# ------------------------------------------------------------------ loaders
def load_pi(run: str, cond: str, repo: str) -> list[Session]:
    out = []
    for f in sorted(glob.glob(str(RUNS / run / cond / "*" / "rep*" / "events.jsonl"))):
        parts = Path(f).parts
        s = Session("pi", repo, parts[-3], int(parts[-2][3:]))
        outputs: dict[str, int] = {}
        pending: list[tuple[Request, list[tuple[Call, str]]]] = []
        for line in open(f, encoding="utf-8"):
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get("type") == "tool_execution_end":
                txt = "".join(x.get("text", "") for x in o["result"].get("content") or [])
                outputs[o["toolCallId"]] = len(txt) // 4
                for _req, ids in pending:
                    for c, cid in ids:
                        if cid == o["toolCallId"]:
                            c.out_tokens = len(txt) // 4
            if o.get("type") != "message_end":
                continue
            m = o["message"]
            role = m.get("role")
            content = m.get("content")
            text = content if isinstance(content, str) else "".join(
                x.get("text", "") for x in (content or []) if isinstance(x, dict))
            if role == "user":
                s.prompts.append(text)
            elif role == "custom" and "[Arbiter]" in text and "context" in text:
                s.packs.append((len(s.prompts) - 1, pack_files(text)))
            elif role == "assistant":
                u = m.get("usage") or {}
                cost = u.get("input", 0) + u.get("cacheWrite", 0) + 0.1 * u.get("cacheRead", 0) + 8 * u.get("output", 0)
                ids = []
                for x in content or []:
                    if x.get("type") != "toolCall":
                        continue
                    a = x.get("arguments") or {}
                    n = x["name"]
                    if n == "read":
                        cs = [Call("read", norm(a.get("path", "")))]
                    elif n in ("edit", "write", "insert_code", "replace_def"):
                        cs = [Call("edit", norm(a.get("path", "")))]
                    elif n == "bash":
                        cs = classify_bash(a.get("command", ""))
                    else:
                        cs = [Call("other", n)]
                    for c in cs:
                        ids.append((c, x.get("id")))
                req = Request([c for c, _ in ids], cost, len(s.prompts) - 1,
                              u.get("input", 0) + u.get("cacheWrite", 0) + u.get("cacheRead", 0))
                pending.append((req, ids))
                s.requests.append(req)
        out.append(s)
    return out


def js_calls(code: str) -> list[Call]:
    calls = []
    for cmd in re.findall(r"exec_command\(\{\s*cmd:\s*(\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`[^`]*`)", code):
        try:
            text = json.loads(cmd) if cmd.startswith('"') else cmd[1:-1]
        except ValueError:
            text = cmd[1:-1]
        calls += classify_bash(text)
    for patch in re.findall(r"\*\*\* (?:Update|Add) File: ([^\n\\]+)", code):
        calls.append(Call("edit", norm(patch.strip())))
    return calls or [Call("other", code[:60])]


def load_codex(run_prefix: str, repo: str) -> list[Session]:
    out = []
    for f in sorted(glob.glob(str(CODEX_SESSIONS / "**" / "*.jsonl"), recursive=True)):
        lines = [json.loads(x) for x in open(f, encoding="utf-8") if x.strip()]
        cwd = next((x["payload"].get("cwd") for x in lines if x.get("type") == "turn_context"), "") or ""
        m = re.search(r"ws[\\/]([a-z]+_[a-z_]+?)-(baseline|full)-(\d+)-", cwd)
        if not m or not m.group(1).startswith(run_prefix) or r"ArbiterBench\ws" not in cwd.replace("/", "\\"):
            continue
        s = Session("codex", repo, m.group(1), int(m.group(3)))
        s.cond = m.group(2)  # type: ignore[attr-defined]
        current: list[Call] = []
        by_call: dict[str, list[Call]] = {}
        for x in lines:
            t, p = x.get("type"), x.get("payload") or {}
            if t == "response_item":
                pt = p.get("type")
                if pt == "message" and p.get("role") == "user":
                    txt = "".join(c.get("text", "") for c in p.get("content") or [])
                    if not txt.startswith("<environment_context>"):
                        s.prompts.append(txt)
                elif pt == "message" and p.get("role") == "developer":
                    txt = "".join(c.get("text", "") for c in p.get("content") or [])
                    if txt.startswith("[Arbiter]") and "context" in txt[:80]:
                        s.packs.append((len(s.prompts) - 1, pack_files(txt)))
                elif pt in ("custom_tool_call", "function_call"):
                    cs = js_calls(str(p.get("input") or p.get("arguments") or ""))
                    current += cs
                    by_call[p.get("call_id", "")] = cs
                elif pt in ("custom_tool_call_output", "function_call_output"):
                    cs = by_call.get(p.get("call_id", ""), [])
                    size = len(json.dumps(p.get("output"))) // 4
                    for c in cs:
                        c.out_tokens = size // max(1, len(cs))
            elif t == "token_usage_record":
                u = p.get("usage") or {}
                inp, cached = u.get("input_tokens", 0), u.get("cached_input_tokens", 0)
                s.requests.append(Request(current, inp - cached + 0.1 * cached + 8 * u.get("output_tokens", 0),
                                          len(s.prompts) - 1, inp))
                current = []
        out.append(s)
    return out


# ------------------------------------------------------------------ analysis
def analyze(sessions: list[Session]) -> dict:
    tag = Counter()
    by_tag_cost: Counter = Counter()
    lookups = 0
    total_cost = removable_cost = attach_cost = 0.0
    reqs = removable = 0
    test_reads = test_read_tokens = 0
    for s in sessions:
        edited: set[str] = set()
        seen_full: set[str] = set()
        shown: dict[str, str] = {}
        prior_out_paths: set[str] = set()
        read_since_edit: dict[str, int] = {}
        pack_by_prompt = defaultdict(dict)
        for pi, files in s.packs:
            pack_by_prompt[pi].update(files)
        last_prompt = -1
        for r in s.requests:
            if r.prompt_idx != last_prompt:
                last_prompt = r.prompt_idx
                shown.update(pack_by_prompt.get(r.prompt_idx, {}))
                for p, how in pack_by_prompt.get(r.prompt_idx, {}).items():
                    if how == "full":
                        seen_full.add(p)
            prompt = s.prompts[r.prompt_idx] if 0 <= r.prompt_idx < len(s.prompts) else ""
            prompt_names = {n.split(".")[-1] for n in IDENT.findall(prompt)} | set(PATHLIKE.findall(prompt))
            reqs += 1
            total_cost += r.cost
            predictable_all = bool(r.calls)
            attach = 0
            req_tags: list[str] = []
            for c in r.calls:
                if c.kind == "read":
                    lookups += 1
                    p = c.target
                    if is_test_path(p):
                        test_reads += 1
                        test_read_tokens += c.out_tokens
                    why = None
                    if p in seen_full and read_since_edit.get(p, 0) == 0:
                        why = "subsumed"
                    elif p in edited:
                        why = "own_edit"
                    elif is_test_path(p) and any(src in edited or src in shown for src in source_of(p)):
                        why = "test_of"
                    elif shown.get(p) in ("excerpt", "outline", "listed"):
                        why = "pack_partial"
                    elif p in prior_out_paths:
                        why = "prev_output"
                    elif any(n and n in p for n in prompt_names):
                        why = "prompt"
                    tag[why or "unpredicted"] += 1
                    req_tags.append(why or "unpredicted")
                    if why is None:
                        predictable_all = False
                    else:
                        attach += c.out_tokens
                    seen_full.add(p)
                    read_since_edit[p] = 0
                elif c.kind == "search":
                    lookups += 1
                    named = any(n and len(n) > 2 and n in c.target for n in prompt_names)
                    tag["search_named" if named else "search_other"] += 1
                    req_tags.append("search_named" if named else "search_other")
                    if not named:
                        predictable_all = False
                    else:
                        attach += c.out_tokens
                elif c.kind == "edit":
                    edited.add(c.target)
                    read_since_edit[c.target] = 1
                    seen_full.discard(c.target)
                    predictable_all = False
                else:
                    predictable_all = False
            if predictable_all:
                for t in req_tags:
                    by_tag_cost[t] += r.cost / len(req_tags)
                removable += 1
                removable_cost += r.cost
                attach_cost += attach
    return {"sessions": len(sessions), "requests": reqs, "lookups": lookups, "tags": tag,
            "removable_requests": removable, "cost": total_cost, "removable_cost": removable_cost,
            "attach_cost": attach_cost, "by_tag_cost": by_tag_cost, "test_reads": test_reads,
            "test_read_tokens": test_read_tokens}


def masking(sessions: list[Session]) -> tuple[float, float, float]:
    """Hide earlier prompts' tool outputs at each new prompt (placeholders), breaking the cache once
    per prompt. Returns (saving, cache-break cost, total cost) in cost-index units."""
    saving = breaks = total = 0.0
    for s in sessions:
        old = 0                       # tokens of tool output from earlier prompts
        this_prompt = 0
        last = -1
        first_ctx = None
        for r in s.requests:
            total += r.cost
            if first_ctx is None:
                first_ctx = r.ctx
            if r.prompt_idx != last:
                if last >= 0:
                    old += this_prompt
                    this_prompt = 0
                    # The prefix changes where the first hidden output sat: everything after it is sent
                    # uncached once (0.9 more than cached), minus what was hidden.
                    breaks += 0.9 * max(0, r.ctx - first_ctx - old)
                last = r.prompt_idx
            if r.prompt_idx >= 1:
                saving += 0.1 * old
            this_prompt += sum(c.out_tokens for c in r.calls)
    return saving, breaks, total


def playbook(sessions: list[Session]) -> dict:
    """Files each task type needed beyond the first pack, and how consistently across reps."""
    per_task: dict[str, list[set[str]]] = defaultdict(list)
    for s in sessions:
        first = dict(s.packs[0][1]) if s.packs else {}
        needed = set()
        for r in s.requests:
            for c in r.calls:
                if c.kind in ("read", "edit") and c.target and first.get(c.target) != "full":
                    needed.add(c.target)
        per_task[s.task].append(needed)
    rows = {}
    for task, sets in per_task.items():
        counts = Counter(p for st in sets for p in st)
        stable = {p for p, n in counts.items() if n >= max(2, len(sets) - 1)}
        rows[task] = {"reps": len(sets), "beyond_pack": round(sum(map(len, sets)) / len(sets), 1),
                      "stable": sorted(stable)}
    return rows


def main() -> None:
    groups = {
        ("pi", "sympy"): load_pi("pi-luna-realrepo-v2", "full", "sympy"),
        ("pi", "date-fns"): load_pi("pi-luna-datefns", "full", "date-fns"),
    }
    cx = load_codex("rr_", "sympy") + load_codex("js_", "date-fns")
    groups[("codex", "sympy")] = [s for s in cx if s.repo == "sympy" and s.cond == "full"]
    groups[("codex", "date-fns")] = [s for s in cx if s.repo == "date-fns" and s.cond == "full"]
    for (agent, repo), ss in groups.items():
        a = analyze(ss)
        net = a["removable_cost"] - a["attach_cost"]
        print(f"\n=== {agent} on {repo} (Arbiter on): {a['sessions']} sessions, {a['requests']} requests, "
              f"{a['lookups']} lookups")
        for k, v in a["tags"].most_common():
            print(f"    {k:14s} {v:4d}")
        share = 100 * a["removable_requests"] / max(1, a["requests"])
        print(f"  removable requests: {a['removable_requests']} ({share:.0f}%), "
              f"their cost {100 * a['removable_cost'] / a['cost']:.1f}% of total, attached content "
              f"{100 * a['attach_cost'] / a['cost']:.1f}% -> net {100 * net / a['cost']:.1f}%")
        print("  removable cost by reason:", ", ".join(f"{k} {100 * v / a['cost']:.1f}%"
                                                        for k, v in a["by_tag_cost"].most_common()))
        print(f"  test-file reads: {a['test_reads']} ({a['test_read_tokens']} tokens of output)")
        sv, br, tot = masking(ss)
        print(f"  hide old outputs at each new prompt: saves {100 * sv / tot:.1f}%, cache breaks cost "
              f"{100 * br / tot:.1f}% -> net {100 * (sv - br) / tot:+.1f}%")
        pb = playbook(ss)
        for task, row in sorted(pb.items()):
            print(f"    {task:22s} beyond first pack/session {row['beyond_pack']:4}  needed in (nearly) every rep: "
                  f"{', '.join(row['stable'][:6])}")


if __name__ == "__main__":
    main()
