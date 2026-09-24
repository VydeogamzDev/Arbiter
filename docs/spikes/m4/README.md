# M4 evidence — rules-only completion gate (v0.1)

| Exit item | Evidence | Result |
| --- | --- | --- |
| False PASS = 0 | 30 end-to-end gate traces (`eval/corpus/gate.yaml`). They include adversarial cases: claims without runs, stale runs, narrowed `-k` runs, finish-check assertions only, added skip markers, deleted test files, weakened asserts, exit-code contradictions, trivially satisfiable or weak recipes, violated "don't change X", uncovered requests | 0 |
| Gate fires on ≤ 2% of non-claim stops | 40 non-claim final messages (`claims.yaml`) plus question-ending stops in the traces | 0 / 40 |
| Severity-weighted false-complete ≤ 50% of stock | stock = every claim accepted | Arbiter 0 vs stock 38 (severity weight) → 0.0 |
| Gating-hook p95 ≤ 300 ms with gate logic | `tests/test_gate.py::test_gating_hook_p95_with_gate_logic_real_daemon`: a real daemon, a session with contracts and a baseline, 40 Stop hooks | Codex `mcp_tool`: p50 11.0 ms, p95 13.8 ms · Claude `http`: p50 29.5 ms, p95 33.5 ms |
| Block wording rule (0017) | `gate.check_wording` on every block in the corpus and tests | 0 violations |
| No gate text persisted as memory (real Claude Code) | `tests/test_real_claude_memory.py` (opt-in, `ARBITER_REAL_CLAUDE=1`): Claude Code 2.1.251, Haiku, block mode, bound 1 | pass: one stop blocked, then allowed; no memory/instruction file contained Arbiter text; global `CLAUDE.md` unchanged; scratch project and its `~/.claude/projects` folder deleted. Cost **$0.09** (4 turns) |
| Breakers fail open (fault injection) | `tests/test_gate.py`: injected gate exceptions, slow-gate latency samples, unparseable runner output | 100% trip; no block and no PASS while open |
| Release artifacts | `uv build` → `arbiter_agent-0.1.0` wheel + sdist; [tool_install_check.json](tool_install_check.json); Claude Code plugin + local marketplace in `packaging/claude-code`; README; CHANGELOG | built and verified locally |
| **Published to PyPI** | — | **pending: needs the owner's PyPI account** (`uv publish`) |

Found and fixed during the install check: WMI-launched daemons ignored `CODEX_HOME` / `CLAUDE_CONFIG_DIR` (decision 0025).

From now on the real Claude Code test runs on Opus 5.5 (`claude-opus-5-5`) by default; the run recorded above used Haiku.
