# M3 evidence — task state + verification

Run `arbiter eval` (or `uv run pytest tests/test_eval_gates.py`). Full report: [exit_gates.json](exit_gates.json).

| §20.17 gate | Corpus | Value | Threshold |
| --- | --- | --- | --- |
| Explicit-requirement recall (quoted or flagged) | 20 cases, 42 requirements (`eval/corpus/contracts.yaml`) | 1.0 | ≥ 0.95 |
| Quote provenance: fabricated, paraphrased, too short, or from another session → rejected | 5 cases | 1.0 | 1.0 |
| Agent can't set status (`status: pass` in a proposal is ignored) | 1 case | 1.0 | 1.0 |
| Test-weakening detection | 23 cases (deletions, skip/xfail/only/xit, removed tests, weakened asserts, fixture/snapshot rewrites, harness filters, neutered test scripts, fewer tests run) | 1.0; 0 missed file deletions | ≥ 0.95; 0 |
| False integrity alarms on benign edits | 5 cases | 0 | reported |
| Runner-parser misreads (FAIL/UNKNOWN read as PASS) | 51 fixtures (pytest, unittest, jest, vitest, mocha, go, cargo, dotnet, tsc, eslint, ruff, mypy, JUnit XML, shell wrappers, contradictions) | 0 (accuracy 1.0) | 0 |
| Loop-alert precision (advisory) | 14 sequences | 1.0 (recall 1.0) | ≥ 0.80 |
| "yes, continue" supersedes contracts | 25 prompts, including resume boundaries | 0 | 0 |
| Concurrent sessions isolated | interleaved sessions in one repo | 1.0 | 1.0 |

Unit coverage lives in `tests/test_task_state.py`: epochs, the append-only intent log, provenance, rule extraction, weak contracts, staleness, the exit-code join from transcript duplicates, flaky/conflicted runs, agent assertions, integrity with and without a baseline, acknowledgements, restart from cursor, and loop facts.

**Caveat.** The corpus is synthetic and was written alongside the rules, so these numbers are a regression floor, not population estimates. Held-out, real-trace cohorts (§20.2) come with M5+ usage data.
