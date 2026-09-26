# Arbiter A/B benchmark

This benchmark measures real coding-agent outcomes with and without Arbiter. It uses the same tasks, the same model and the same prompts, with fresh state for every run.

## Layout

- `make_tasks.py` generates `tasks/<id>/`:
  - `repo/`: what the agent sees;
  - `hidden/`: acceptance tests, one per stated requirement, that the agent never sees;
  - `solution/` and `sloppy/`: overlays used only to validate scoring.
- `conditions.py` defines the conditions: which Arbiter pieces a run gets.
- `harness.py` runs (task × condition × rep) jobs and scores them.
- `score.py` handles scoring: hidden tests, test integrity and completion claims.
- `report.py` produces the per-condition table, paired differences against baseline with 95% bootstrap CIs over tasks, and category and task breakdowns.

## Suites

- `tasks/` (dev, 10 tasks): used while tuning Arbiter.
- `tasks_heldout_v1/` (10 tasks, frozen in `bbaa486` before any Arbiter run): never tune against it. If a result on it ever drives an Arbiter change, retire it to dev and write `heldout_v2` (`make_heldout_v1.py` is the template).

## Dev tasks (10)

| id | category | what it probes |
|---|---|---|
| discount_tiers | requirements | 5 bundled requirements, plus "add tests" |
| wc_json_flag | requirements | code change plus README and CHANGELOG obligations |
| parse_duration | false_complete | spec with many error cases; easy to claim done early |
| fib_fast | false_complete | "fast" and "no recursion limit"; memoized recursion still overflows |
| tz_fix | integrity | a failing test that is tempting to skip; the bug is in code |
| pagination_ci | integrity | "fix it quickly so we can ship"; tempting to edit the test |
| invoice_date_bug | retrieval | the bug is in a helper three files away, in a 25-file repo |
| slug_rename | api_compat | rename, keeping a deprecated alias that warns |
| reversible_migration | high_risk | SQLite downgrade must restore the schema and keep rows |
| greeter_scope_change | scope_change | the second turn reverses part of the first turn |

## Conditions

| name | Arbiter pieces |
|---|---|
| baseline | none |
| full | hooks, MCP tools, completion gate in block mode, auto file context, status summaries, encoder sensor (shadow) |
| gate_only | hooks, MCP contract tools, block-mode gate |
| context_only | hooks, auto file context and status summaries (annotate gate, no MCP) |
| tools_only | MCP tools only (nothing enforced) |
| observe_only | hooks in annotate mode with nothing injected: pure overhead |

The gateway (M10) is not exercised: these tasks have no upstream MCP servers. Its benefit is measured separately by `arbiter gateway bench`.

## Metrics

- **Task success:** every hidden test passes.
- **Requirement coverage:** the fraction of hidden tests that pass.
- **False "done" claims:** the task failed and the final message doesn't admit it. This uses a regex heuristic; the final messages are kept for review.
- **Test tampering:** a given test file was deleted, gained a skip or xfail marker, or was edited so that the original tests no longer pass.
- **Cost, turns, tokens and wall time:** taken from `claude -p --output-format json`.
- **Arbiter activity:** stop blocks, ledger verdicts and sensor rows, read from each run's own database.

## Results (Opus 5.5, `claude -p`)

| suite | runs | cost/run | turns | wall | success |
|---|---|---|---|---|---|
| dev, pilot-1 (before the fixes) | 10 vs 10 | **+103%** | +116% | +121% | 10/10 both |
| dev, pilot-4 (context pack, evidence-first gate) | 10 vs 10 | **-19%** | -40% | -30% | 10/10 both |
| held-out v1, 2 reps (narrow command allowlist, see below) | 20 vs 20 | -26% [95% CI -40% to -15%] | -38% | -21% | 20/20 both |
| **held-out v1, fair harness (verify-opus)** | 10 vs 10 | **-14%** [95% CI -26% to -5%] | **-28%** | **-18%** | 10/10 both |
| quality v1, **Sonnet 5**, fair harness, 2 reps (verify-sonnet5) | 20 vs 20 | **+11%** [95% CI -5% to +27%] | -7% | +30% | 16/20 vs 18/20 |

**Correction (2026-09-26).** The first held-out numbers overstated the gain:
- The harness's command allowlist denied compound shell commands. Baseline agents lost calls to those denials while orienting (16 denials vs 3 with Arbiter).
- The first run of each condition paid a cold prompt-cache write.

The harness now allows shell commands and warms the cache per condition. The fair Opus figure is -14%.

Decomposed with `python -m bench.analyze <run>`:
- **Opus** saves on re-read context (calls -32%, orientation 11 -> 2, reads 11 -> 5).
- **Sonnet 5** thinks 53% longer with the pack in context and runs the tests twice as often (9 -> 19), and it still re-reads files before editing. That outweighs the orientation it skips (21 -> 0).
- The `full_map` condition tests a map-only pack (`retrieval.auto_context_pack_contents: false`) for that case.

**Map-only pack (`full_map`, same baselines):**

| model | full pack | map-only |
|---|---|---|
| Opus 5.5 (held-out) | **-14% cost**, reads 11 -> 5 | +1% cost; reads 11 -> **26** (it opens every ranked file) |
| Sonnet 5 (quality, 2 reps) | +11% cost, thinking +53% | **+2% cost**, thinking +8% |

The two models need opposite settings: Opus's whole gain comes from the file contents, and those same contents are what make Sonnet think longer. No single setting wins on both.

Sonnet also runs the tests about once more per task whenever the repo map lists test files. That's arguably correct verification, so the map doesn't hide them.

### Retest after auto-test and the per-model pack (2026-09-26, 1 rep, fair harness)

These runs used Arbiter's own test runs after edits and at claims, plus the pack chosen per model. Every run started a fresh daemon, so the model was always unknown at the first prompt and the pack waited for the first tool hook. That penalty is fixed in `edf9a1c`: the model is remembered, and the map goes out at the prompt with contents following.

| model | suite | cost | turns | wall | success |
|---|---|---|---|---|---|
| Opus 5.5 | held-out (10) | **-9%** [95% CI -16% to -2%] | -24% | -6% | 10/10 both |
| Opus 5.5 | large-repo (5) | **-9%** [-18% to -0%] | -25% | -15% | 5/5 both |
| Sonnet 5 | held-out (10) | **-16%** [-29% to -3%] | -31% | -8% | 10/10 both |
| Sonnet 5 | large-repo (5) | **-25%** [-54% to +0%] | -14% | -24% | 5/5 both |

What moved (`bench.analyze`):
- Agent test runs fell (Sonnet 8 -> 0 and Opus 13 -> 7 on held-out) because Arbiter's results came back with the edits.
- Sonnet's extra shell checks fell 7 -> 2.
- Sonnet went from +11% to -16% on held-out.
- Opus lost some of its earlier orientation saving (orientation 11 -> 9, versus 11 -> 2 when the pack reached the first prompt). That's the deferral penalty fixed in `edf9a1c`.

`lr_setting_rename`'s hidden test was fixed after the run: it only accepted the deprecation warning at lookup, and the prompt also allows it at `configure()`. All four runs were rescored, and it passes in all of them.

The per-model pack decision is also in `edf9a1c`. Codex hook payloads carry the model. For Claude Code, the model is known from the transcript after the first reply, so the pack can arrive with the first tool hook.

Held-out v1 in detail:
- 9 of 10 tasks were cheaper and one (`mailer_kwonly`) was even.
- The context pack was delivered in 20/20 runs.
- The gate blocked nothing: no run had missing evidence.

Opus 5.5 solved every task in both suites with or without Arbiter, so these numbers measure efficiency only.

### Quality (`tasks_quality_v1/`, frozen in `4f301d0`, 1 rep each)

These 10 tasks sit nearer the model's limit: a 10-rule pricing spec, undo/redo grouping, a four-turn evolving API, cache invalidation, an order-dependent flaky test, path-traversal hardening, a hand-written CSV parser, config docs obligations, month-end recurrence and strict roman numerals.

| effort | success (baseline / Arbiter) | requirement coverage | false "done" | tampering | cost | turns |
|---|---|---|---|---|---|---|
| default | 9/10 / 9/10 | 98% / 98% | 1 / 1 | 0 / 0 | -10% | **-32%** |
| low | 10/10 / 9/10 | 100% / 98% | 0 / 1 | 0 / 0 | -3% | -22% |
| default, **Sonnet 5** | 8/10 / 8/10 | 97% / 97% | 2 / 2 | 0 / 0 | +1% | ±0% (output tokens +18%, wall +22%) |

On Sonnet 5 both conditions failed the same two tasks (`fx_flaky_ci`, `undo_redo_buffer`), and every failure was reported as done.

The efficiency gain didn't carry over. Sonnet barely explores, reading one file and writing in 3–6 turns, so the pack has few orientation turns to save. The extra context made it write more instead.

The single gate block on Sonnet was a false positive: a scratch file written outside the repo counted as a code change. Excluding files outside the project fixes it. Because that finding comes from this suite, quality_v1 counts as a dev set for any future quality claim.

**No measurable quality difference.** The only failure was the same task (`fx_flaky_ci`), where the agent kept a cache that ignores which rate table it was given. It happened in 3 of the 4 runs, in both conditions. Whether a run fails there depends on whether the agent removes the cache, not on Arbiter.

**The gate fired once in 40 runs.** At low effort in `todo_four_turns`, it caught a stop with no test run after the last edit; the agent re-ran the tests and passed.

With Opus 5.5 there is almost nothing for the gate to catch. Measuring quality gains needs a weaker model or much longer tasks.

## Running

```bash
python bench/make_tasks.py
python -m bench.harness run --agent fake-solution --conditions baseline,full,gate_only,context_only,tools_only,observe_only   # free check
python -m bench.harness run --agent claude --conditions baseline,full --reps 1 --jobs 2       # paid: Opus 5.5
python -m bench.harness run --suite heldout_v1 --agent claude --conditions baseline,full --reps 2 --jobs 3
python -m bench.harness report D:/ArbiterBench/runs/<run_id>
```

Results go to `D:/ArbiterBench/runs/<run_id>/<condition>/<task>/rep<n>/`: `result.json`, `final.diff`, the Claude transcript, the Arbiter home and `settings.json` / `mcp.json`. You can override the location with `--out` or `ARBITER_BENCH_OUT`. Re-running the same `--run-id` skips finished runs.

## Isolation

Each run uses:
- `--setting-sources project`, so your user settings, including your real Arbiter hooks, are not loaded;
- `--strict-mcp-config`, so only the condition's MCP servers load;
- a fresh Arbiter home and daemon, stopped afterwards;
- a throwaway git workspace.

Claude's per-project transcript folder for the workspace is copied into the results and then deleted. After each run the harness counts benchmark sessions in your real Arbiter database; the count must not change, and the result is recorded in `run.json` as `real_install_bench_sessions_added`.
