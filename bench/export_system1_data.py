"""Training data for a local system-1 model (K2 Horizon 3.7B / 7B, fine-tuned on the 3080 Ti) from what the
benchmark has already recorded. One JSONL file per role, chat-format rows ({"messages": [...], "meta": {...}}),
prompts exactly as ``arbiter_agent.system1`` sends them, so the fine-tuned model drops in behind the ``openai``
backend.

- effort: every real-repo / hard_v1 prompt; label ``low`` when Codex on gpt-6-sol at low effort passed the task
  at least as often as at high effort (else ``high``).
- judge: every finished Codex session's final diff and requests; label ``OK`` when the hidden tests passed, else
  the failing requirement names (weak labels: an ambiguous requirement also counts as a failure).
- handoff: each prompt of the long_v1 sessions with the ones before it; label from the known task boundaries.
- brief / judge (teacher): gpt-6-luna's recorded answers from system-1 debug logs, for distillation.

    python -m bench.export_system1_data --out D:/ArbiterBench/system1-data
"""

from __future__ import annotations

import argparse
import glob
import json
from collections import defaultdict
from pathlib import Path

import yaml

from arbiter_agent.system1 import EFFORT_PROMPT, HANDOFF_PROMPT, JUDGE_PROMPT

BENCH = Path(__file__).resolve().parent
RUNS = Path("D:/ArbiterBench/runs")
SUITES = {"rr_": BENCH / "tasks_realrepo_v1", "js_": BENCH / "tasks_realrepo_js_v1", "hd_": BENCH / "tasks_hard_v1",
          "hf_": BENCH / "tasks_hard_v1"}


def task_yaml(task: str) -> dict | None:
    for prefix, suite in SUITES.items():
        if task.startswith(prefix) and (suite / task / "task.yaml").is_file():
            return yaml.safe_load((suite / task / "task.yaml").read_text("utf-8"))
    return None


def row(prompt: str, answer: str, **meta: object) -> dict:
    return {"messages": [{"role": "user", "content": prompt}, {"role": "assistant", "content": answer}], "meta": meta}


def results(pattern: str) -> list[dict]:
    out = []
    for f in glob.glob(str(RUNS / pattern)):
        try:
            r = json.loads(Path(f).read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if not r.get("harness_error") and r.get("score"):
            r["_dir"] = str(Path(f).parent)
            out.append(r)
    return out


def effort_rows() -> list[dict]:
    by_task: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    for r in results("codex-sol-*/full*/*/rep*/result.json"):
        efforts = {t.get("effort") for t in r.get("turns", [])} or {None}
        if efforts == {None}:                      # older runs: the effort is in the run's name
            name = r["_dir"].replace("\\", "/").split("/runs/")[-1].split("/")[0]
            efforts = {next((e for e in ("low", "medium", "high") if f"-{e}-" in name), None)}
        if len(efforts) == 1 and next(iter(efforts)) in ("low", "high"):
            by_task[r["task"]][next(iter(efforts))].append(bool(r["score"]["full_pass"]))
    rows = []
    for task, runs in sorted(by_task.items()):
        if not runs.get("low") or not runs.get("high"):
            continue
        low = sum(runs["low"]) / len(runs["low"])
        high = sum(runs["high"]) / len(runs["high"])
        label = "low" if low >= high else "high"
        meta = task_yaml(task) or {}
        for i, p in enumerate(meta.get("prompts") or []):
            rows.append(row(EFFORT_PROMPT.format(request=p[:3000]), label, task=task, prompt_index=i,
                            low_pass=round(low, 2), high_pass=round(high, 2)))
    return rows


def judge_rows() -> list[dict]:
    rows = []
    for r in results("codex-*/*/*/rep*/result.json"):
        meta = task_yaml(r["task"])
        diff_path = Path(r["_dir"]) / "final.diff"
        if not meta or not diff_path.is_file():
            continue
        diff = diff_path.read_text("utf-8", errors="replace")[:16000]
        if not diff.strip():
            continue
        requests = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(meta["prompts"]))
        failed = [k for k, v in (r["score"].get("requirements") or {}).items() if not v]
        answer = "OK" if r["score"]["full_pass"] else "\n".join(f"{i + 1}. requirement not met: {k}"
                                                                for i, k in enumerate(failed[:3]))
        rows.append(row(JUDGE_PROMPT.format(requests=requests, diff=diff, tests="PASS"), answer, task=r["task"],
                        run=r["_dir"].split("runs")[-1], weak_label=True))
    return rows


def handoff_rows() -> list[dict]:
    rows = []
    for tid in ("long_sympy", "long_datefns"):
        meta = yaml.safe_load((BENCH / "tasks_long_v1" / tid / "task.yaml").read_text("utf-8"))
        prompts = meta["prompts"]
        for i in range(1, len(prompts)):
            start = i - i % 3 if i % 3 else i - 3
            earlier = "\n".join(f"{j + 1}. {p[:400]}" for j, p in enumerate(prompts[start:i]))
            rows.append(row(HANDOFF_PROMPT.format(earlier=earlier, request=prompts[i][:1500]),
                            "new" if i % 3 == 0 else "continue", task=tid, prompt_index=i))
    return rows


def teacher_rows() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for log in glob.glob(str(RUNS / "*" / "*" / "*" / "rep*" / "arbiter-home" / "logs" / "system1.jsonl")):
        for line in open(log, encoding="utf-8"):
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get("answer"):
                out[f"{o['role']}_teacher"].append(row(o["prompt"], o["answer"], teacher="gpt-6-luna", log=log))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("D:/ArbiterBench/system1-data"))
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    sets = {"effort": effort_rows(), "judge": judge_rows(), "handoff": handoff_rows(), **teacher_rows()}
    for name, rows in sets.items():
        with (a.out / f"{name}.jsonl").open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"{name:16s} {len(rows):5d} rows -> {a.out / (name + '.jsonl')}")


if __name__ == "__main__":
    main()
