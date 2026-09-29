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

The per-model pack decision is also in `edf9a1c`.

### Retest 2: pack at the prompt, "Arbiter runs the tests" note, virtualenv fix (`ef5d520`, 1 rep, same baselines)

| model | suite | cost [95% CI] | calls | agent test runs | success |
|---|---|---|---|---|---|
| Sonnet 5 | held-out | **-21%** [-35% to -9%] | -36% | 8 -> 2 | 10/10 |
| Sonnet 5 | large-repo | **-31%** [-70% to -6%] | -34% | 0 -> 0 | 5/5 |
| Opus 5.5, model known (normal use) | held-out | **-12%** [-21% to -4%] | -24% | 13 -> 3 | 10/10 |
| Opus 5.5, model known (normal use) | large-repo | **-10%** [-16% to -3%] | -22% | 5 -> 0 | 5/5 |
| Opus 5.5, client's first session | held-out / large-repo | -1% / -5% | ±0% | | 10/10, 5/5 |

- **First session vs normal use.** An installed daemon remembers each client's model, so only a client's first session starts without it. In that session Opus gets the map pack and reads every listed file (reads 11 -> 29). Runs seed the model by default to measure ordinary sessions; `--first-session` measures the first one.
- **Why Opus's saving caps near 10-12%.** About two thirds of an Opus run is Claude Code's own fixed cost: the session-context cache write on the first call (~30%) and its ~30k-token system prompt re-read on every call (~31%). Arbiter cut Opus's calls by 22-24%, which only reaches the other third.
### Retest 3: plus `arbiter slim` lean (`687bf3c`, condition `full_slim`, same baselines, $2.72)

| model | suite | cost [95% CI] | calls | success |
|---|---|---|---|---|
| Opus 5.5 | held-out | **-41%** [-57% to -26%] | -6% | 10/10 |
| Opus 5.5 | large-repo | **-52%** [-60% to -42%] | -38% | 5/5 |
| Sonnet 5 | held-out | **-60%** [-80% to -44%] | -36% | 10/10 |
| Sonnet 5 | large-repo | **-63%** [-98% to -39%] | -23% | 5/5 |

- **What slim changes.** It cuts Claude Code's own per-call context from ~39k to ~16.5k tokens. On Opus held-out, cache writes fell 46% and context re-reads fell 59%.
- **Not measured yet: slim alone.** Slim would also help without the rest of Arbiter, and a baseline+slim run would show the split.
- **The gain depends on the client.** Much of it comes from the desktop app's large tool set (Artifact is ~11k tokens), so a plain terminal Claude Code saves less.

- **Why Sonnet gains more.** Sonnet's baseline spends more calls on orientation and spot checks, and those are exactly what the map pack and auto-test remove. Codex hook payloads carry the model. For Claude Code, the model is known from the transcript after the first reply, so the pack can arrive with the first tool hook.

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

## Codex, gpt-6-luna (2026-09-27)

`--agent codex`, default (medium) reasoning, 1 rep, Arbiter off (`baseline`) vs on (`full`, no slim
mode: Codex has no equivalent). `export_scope_change` skipped (multi-prompt). Cost index = uncached
input + 0.1 x cached input + 8 x output (OpenAI price ratios; the account is on a ChatGPT plan).

| suite | tasks | success off / on | cost index | input tokens | output tokens | tool calls | Arbiter cheaper |
|---|---|---|---|---|---|---|---|
| heldout_v1 | 9 | 9/9 / 9/9 | 222k -> 193k (-13%) | -15% | -13% | 40 -> 31 | 8/9 |
| largerepo_v1 | 5 | 5/5 / 5/5 | 113k -> 94k (-17%) | -20% | -4% | 23 -> 17 | 5/5 |

No tampering or false "done" claims in either condition; no leaks into the real install. The one
loss (mailer_kwonly, +44%) re-read the files the pack already held and made three extra edits.
Codex's fixed context is ~15k tokens (Claude Code: ~39-52k), so the saving is close to the
no-slim Claude Code result without cutting anything from the client.

## Pi, gpt-6-luna (2026-09-27)

`--agent pi` (Pi 0.87.1, provider openai-codex, thinking medium), 1 rep, Arbiter off vs on through
the Pi extension (hooks only: Pi has no MCP). All 10 held-out tasks run (Pi sessions can continue).
Same cost index as Codex.

| suite | tasks | success off / on | cost index | input tokens | output tokens | tool calls | Arbiter cheaper |
|---|---|---|---|---|---|---|---|
| heldout_v1 | 10 | 10/10 / 10/10 | 149k -> 112k (-25%) | -43% | -24% | 79 -> 36 | 8/10 |
| largerepo_v1 | 5 | 5/5 / 5/5 | 101k -> 48k (-52%) | -68% | -41% | 50 -> 25 | 5/5 |

No tampering or false "done" claims; no leaks. The large-repo figure includes one expensive
baseline run (lr_rounding_cent 41.8k); without it the saving is -35%. Losses: stock_cancel_bug +2%,
token_hashing +8%.

Harness and Arbiter together, on the tasks both ran (cost index, sums):

| | Codex, Arbiter off | Codex, Arbiter on | Pi, Arbiter off | Pi, Arbiter on |
|---|---|---|---|---|
| heldout_v1 (9 tasks) | 222k | 193k | 134k | 103k (-54% vs Codex off) |
| largerepo_v1 (5 tasks) | 113k | 94k | 101k | 48k (-57% vs Codex off) |

Pi's fixed context is small, so exploration is most of a baseline run's cost, and that is what the
context pack replaces (tool calls halve).

## Pi large-repo ablation, after the pack/test-window fixes (2026-09-27)

`--agent pi --suite largerepo_v1 --reps 3`, gpt-6-luna, 4 conditions, 60 runs, 0 errors. Fixes in
af0101a: pack heading no longer calls files a snapshot that may change; weak picks in large repos
shown as outlines; Pi's post-edit test window 8 s.

| condition | cost index (sum) | vs baseline | 95% CI (tasks) | input tokens | output | tool calls | success |
|---|---|---|---|---|---|---|---|
| baseline | 290k | | | 511k | 9.7k | 136 | 15/15 |
| full | 130k | **-55%** | [-67%, -37%] | -70% | -33% | 61 | 15/15 |
| pack_only | 156k | -46% | [-64%, -20%] | -63% | -18% | 84 | 15/15 |
| tests_gate_only | 247k | -15% | [-22%, -2%] | -19% | -10% | 114 | 15/15 |

The two parts add up (0.54 x 0.85 = 0.46 of baseline, measured 0.45). No stop blocks, false "done"
claims or tampering anywhere, so the gate's share here is nil; tests_gate_only's saving is the
agent no longer running the tests itself (own pytest runs: baseline 14, pack_only 13, full 3,
tests_gate_only 0; Arbiter results delivered: full 20, tests_gate_only 26). Re-reads of files the
pack showed in full: pack_only 18, full 13 in 15 runs (1-rep run before the fix: 7 in 5).
lr_rounding_cent's expensive baseline is consistent (34-48k over 3 reps), not an outlier: its
first search returns ~46k characters. Wall time: full -3%, tests_gate_only +12%.

## Real repo: sympy 1.14.0, 3-prompt sessions, Pi gpt-6-luna (2026-09-27)

`--agent pi --suite realrepo_v1 --reps 3` (make_realrepo_v1.py): five sessions of three follow-up
prompts each on sympy (2,033 files, 622 test files), hidden tests on the final state, index and
bytecode cache built before the agent starts (an installed daemon keeps both). 30 runs, 90 prompts.

The first attempt (before 8ab11af) cost more with Arbiter than without, and was stopped: the gate
blocked every claim (integrity scan timed out on 622 test files), Arbiter's test detection missed
sympy's nested tests, the pack pinned the wrong file ("SymPy's" matched a class named SymPy) and was
re-sent at every follow-up prompt, and cold test runs missed Pi's post-edit window.

| | baseline | full | change |
|---|---|---|---|
| cost index (sum) | 850k | 590k | **-31%** (95% CI over tasks -42% to -15%) |
| input tokens | 2.02M | 1.32M | -35% |
| output tokens | 40k | 28k | -29% |
| tool calls | 312 | 155 | -50% |
| agent wall time | 1,781 s | 1,528 s | -14% |
| sessions passing all hidden tests | 14/15 | 13/15 | |
| agent's own pytest runs | 53 | 0 | Arbiter delivered 86 results |
| stop blocks / tampering | 0 / 0 | 0 / 0 | |

By prompt position: first prompt -25%, second -32%, third -35%. Every task was cheaper at the median
(iter_keyfunc 98k -> 42k; ordinal_words 41k -> 40k). Failures: baseline missed an export
(digital_root not in sympy.ntheory.__all__); both full failures are the same ambiguous requirement
(ordinal(-25, words=True): the prompt says negatives get "minus ", the hidden test expects the
numeric fallback "-25th" beyond the word table), which all three baseline runs read the other way.

## Seven follow-ups (2026-09-27): what each one bought

1-3 on sympy (Pi gpt-6-luna, `realrepo_v1`, 3 reps; baseline reused from the previous run; 13 paired
sessions, two excluded, below):

| | cost vs baseline | 95% CI | requests | edits (batched) | searches | passed |
|---|---|---|---|---|---|---|
| full, before | -28% | -39% to -16% | 176 | 84 (0) | 23 | 12/13 |
| full, items 1-3 | -31% | -40% to -15% | 167 | 74 (0) | 17 | 12/13 |
| full + Pi edit tools (2) | -22% | -32% to -8% | 175 | 80 (2) | 18 | 11/13 |

- 1 (batching guideline): no effect. gpt-6-luna made 0 of 74 edits in a batch.
- 2 (insert_code / replace_def, opt-in): used 15 times, output rose, and the only two sessions of
  ~200 Pi runs that ever hung were in this condition: the model's first bash call degenerated into
  an endless `} } }` / `* * *` stream (25-minute timeouts, excluded above). Harmful: stays off.
- 3 (follow-up packs, class outlines, export files, module pins): fewer searches (23 -> 17) and
  requests; within noise overall.

4, Claude Code on sympy (Opus 5.5, 1 rep, no slim, $9.17 total): baseline $4.49, full $4.67 (+4%).
Calls -13%, agent test runs 17 -> 6, cached reads -5%, output -5%, but cache writes +16%: everything
Arbiter injects is new content, and Claude caches it at 2x input. Codex on sympy was not run: its
3-prompt sessions need a benchmark-only Codex sign-in (the one-shot mode can't resume, and resuming
writes into the real ~/.codex).

5-6: targeted jest/vitest/Go test runs and brace-language excerpts; the gate stops blocking when no
test covers the change (measured: the gate costs 20-80 ms per claim once a result exists).

7, a repo Arbiter was never tuned on: date-fns 4.1.0 (TypeScript, vitest, 1,727 files;
`realrepo_js_v1`, tasks committed before any run), Pi gpt-6-luna, 3 reps, 30 sessions:

| | baseline | full | change |
|---|---|---|---|
| cost index | 817k | 567k | **-31%** (95% CI -42% to -20%) |
| input tokens | 2.19M | 1.16M | -47% |
| tool calls | 299 | 215 | -28% |
| sessions passing all hidden tests | 15/15 | 13/15 | |

By prompt: first -37%, second -24%, third -27%. Arbiter's vitest runs reached the agent 59 times.
Failures (both full): a wrong rounding formula in a new function with no test file; and a syntax
error Arbiter should have caught: 14 quick edits pushed the broken file's test out of the 6-file
related run, and a test file that fails to load was parsed as UNKNOWN. Both fixed after the run
(41b617b), not re-measured.

## Codex on the real repos, 3-prompt sessions (2026-09-28)

`--agent codex` (gpt-6-luna, default reasoning) through a benchmark-only Codex home, sessions
resumed with `codex exec resume`; 3 reps, 30 sessions per repo, no leaks into the real ~/.codex.

| repo | cost index | 95% CI | tool calls | by prompt (1st/2nd/3rd) | passed off / on |
|---|---|---|---|---|---|
| sympy (`realrepo_v1`) | 2.07M -> 1.82M, **-12%** | -22% to -6% | 179 -> 126 | -16% / -12% / -11% | 15/15 / 15/15 |
| date-fns (`realrepo_js_v1`) | 2.13M -> 1.70M, **-20%** | -31% to -8% | 163 -> 109 | -27% / -21% / -17% | 15/15 / 15/15 |

No stop blocks, tampering or false "done" claims. Smaller than Pi's -31% on the same repos because
Codex re-sends ~15k tokens of fixed context with every request. The date-fns runs include 41b617b
(vitest load failures, related-run coverage); the Pi date-fns run did not.

## Tried and dropped (2026-09-28): predict-ahead attachments

Usage lists for named symbols, the target's own test file, the changed region after each edit and a
Ponytail-style write-less note (120 paid Pi/Codex sessions on sympy and date-fns): the targeted
lookups went away but requests didn't (Pi sympy 202 -> 199), and the bigger pack cost more. Every
cell was equal or worse than the plain pack (Pi date-fns -31% -> -24%, Codex date-fns -20% -> -11%).
Reverted.

### Correction (2026-09-28): Codex multi-prompt costs

`codex exec resume` reports the thread's cumulative usage, and the harness summed it per turn, so
prompt 1 was counted three times in 3-prompt sessions (fixed in `run_codex`; `thread_usage` keeps
the raw figure). Recomputed from the last turn's cumulative usage, full vs baseline: sympy **-11%**
(95% CI -20% to -3%; not -12%), date-fns **-17%** (-28% to -5%; not -20%). By prompt: sympy -16% /
-3% / -9%, date-fns -27% / -8% / -4%. Arbiter's Codex savings are almost all in the first prompt.
Pi and one-prompt Codex runs were not affected.

## Pi apply_patch, Codex map-only pack, Codex slim (2026-09-28, each with a same-run `full` control)

gpt-6-luna, 3 reps, 15 sessions per cell, cost vs the reused baselines (Codex costs per-turn, after
the cumulative-usage fix):

| agent / repo | full (same run) | variant | tool calls full -> variant | passed |
|---|---|---|---|---|
| Pi / sympy | -36% | apply_patch -38% | 143 -> 112 | 14 / 14 |
| Pi / date-fns | -40% | apply_patch -34% | 182 -> 137 | 12 / 15 |
| Codex / sympy | -16% | map-only 0%, slim -5% | 117 -> 159 / 136 | 15 / 15 |
| Codex / date-fns | -11% | map-only -2%, slim +5% | 119 -> 150 / 132 | 15 / 15 |

No variant beats its control on cost. Pi's apply_patch (opt-in `ARBITER_PI_APPLY_PATCH=1`) merges
edits into fewer calls but not cheaper sessions. For Codex the pack's file contents matter: without
them Codex made ~30% more tool calls. Run-to-run noise is large: the same `full` measured -27% and
-36% (Pi sympy), -17% and -11% (Codex date-fns) on different runs, so only same-run comparisons count.
Pi's cache misses: Pi bills ~2x its new input as uncached (274k vs 135k new tokens on sympy), 70-90%
of it within a prompt, with or without Arbiter; Codex has almost none.

## Codex pack content (2026-09-28, same-run control): no gain from more

The target's own test file in the pack (`full_tests`) and a 6,000-token pack without outlines
(`full_big`): Codex sympy -12% (control) / -10% / -5%, date-fns -9% / -6% / -13%, all 14-15/15 passed,
all within noise. Contents matter (map-only lost 9-16 points), but more than the 2,500-token pack
doesn't pay. Code reverted.

## Model and effort: the lever (2026-09-29)

Codex + Arbiter (`full`), 3 reps, 15 sessions per cell, priced at API rates per 1M tokens (Astra
$10/$1/$50, Sol $2/$0.20/$10, Luna $0.10/$0.01/$0.50 input/cached/output):

| model / effort | date-fns $/session | passed | sympy $/session | passed |
|---|---|---|---|---|
| gpt-6-sol / high | 0.347 | 15/15 | 0.231 | 12/15 |
| gpt-6-sol / medium | 0.315 | 14/15 | 0.202 | 11/14 |
| gpt-6-sol / low | 0.164 | 13/15 | 0.140 | 12/15 |
| sol/high main, luna sub-agents (`full_delegate`) | 0.260 | 13/15 | 0.214 | 10/15 |
| gpt-6-luna / medium | 0.005 | 15/15 | 0.005 | 15/15 |

On these tasks luna is 47-70x cheaper than sol/high and passes at least as often (sympy's sol
failures are the ambiguous negative-ordinal requirement). Sol/high also works much harder: 3-5x the
tool calls. Low effort halves sol's cost. Delegating implementation to luna sub-agents (via an
AGENTS.md request) saves 7-25% and loses pass rate: the sol main agent still reviews at length.
Codex hooks can't change the model or effort of a turn (tested: model fields in UserPromptSubmit
output are ignored); Pi extensions can (`pi.setModel`, `pi.setThinkingLevel`).
