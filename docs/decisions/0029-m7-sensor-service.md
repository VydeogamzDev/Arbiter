# 0029 — M7 sensor service: scored options behind one interface, shadow-only until measured

- Status: Accepted
- Date: 2026-09-24
- Spec: §4.4.5, §7.1–§7.9
- Builds on: [0026](0026-sensor-backends-model-tiers-and-fine-tuning.md)

## Context
Decision 0026 chose the sensor's backends and model tiers. This records how the service is built, and what it's allowed to do before the benchmark (M7.8) has been run on real model files. The GPU is in repair, so everything here had to work, and be tested, without weights.

## Decisions
1. **One result shape for every backend** (`semif/types.ScoreResult`): options in the order asked, normalized scores, model, revision, backend, precision, template version, tokenizer, the hash of the state text actually sent, latency, cache mode, and a `hard_label` flag. Callers get a `Judgment`: an orientation-corrected aggregate, or an abstention with a reason. Its `score_kind` is `model_score` until calibration exists (§7.5).
2. **Backends:**
   - `null`: abstains on everything. It's what runs until `arbiter semif enable`, so rules-only mode is the default and fully functional.
   - `encoder` (tier 0): GLiNER2.5-Decide through the optional `gliner2` package (`arbiter-agent[encoder]`). A reported confidence becomes a score; a bare label is marked `hard_label`, which mirroring treats as uninformative.
   - `llama_cpp` (decoder tiers): a loopback-only `llama-server`. It does one forward pass with `n_predict: 1` and top-20 token probabilities, and scores each option by its letter's probability. It abstains when less than half the mass is on option letters. It uses `/tokenize` for exact budgets, `cache_prompt` for prefix reuse, and per-request `lora` for adapters, and parses both the older `probs` and newer `top_logprobs` response formats.
   - Anything that can't be constructed (package missing, server down) degrades to `null` with the reason, never an exception.
   - The BF16 SemIf reference backend waits for the GPU.
3. **Budgeting before model work** (§7.2). The envelope is reserved first, sized for the widest option order and paraphrase. Unpinned state is dropped lowest priority first, and pinned state (the user's request, pending tool state) is never dropped. An impossible budget is rejected with no model call. Without the backend's tokenizer the count is the UTF-8 byte length, a guaranteed upper bound for byte-level BPE, never a character heuristic.
4. **Validation turns anything malformed into an abstention** (§7.3). That covers a wrong option order or count, NaN or infinite scores, out-of-range values, scores that don't sum to 1, a wrong template version, an unnamed model or revision, a revision other than the pin (`semif.model_revision`), and a state-hash mismatch. Invalid results count against the family's breaker.
5. **Mirroring** (§7.4). Binary questions are scored in both option orders, plus a paraphrase when impact is high. The service abstains when the variants disagree on the positive option by more than 0.25, when the margin is under 0.20, or on hard labels. Variants are treated as correlated measurements, not votes.
6. **Reliability:**
   - a bounded priority queue that sheds rather than blocks;
   - a deadline on every call;
   - per-family circuit breakers (5 failures, 5-minute cooldown);
   - per-backend back-off after errors (5 s, 30 s, 2 min, 10 min).
7. **Shadow only.** The engine calls `decision_listeners` after two rule decisions: the goal-epoch decision on a prompt (skipped for a session's first prompt) and the claim classification on stop. The shadow harness enqueues a question and returns. Judgments are written to `sensor_log` beside the rule's decision and are never applied. They're the calibration data M8's cascade and R6's fine-tuning will use. A listener that raises is counted and ignored.
8. **`arbiter semif status | enable | disable | bench`:**
   - `enable` detects NVIDIA GPUs with `nvidia-smi`, checks whether `gliner2` is importable, and proposes tier 0 plus the largest decoder tier that fits the VRAM. It prints the `llama-server` command, and writes config only with `--yes`.
   - It never downloads anything itself. Model weights come from `gliner2` on first use, or from a GGUF file the user fetches.
   - `bench` runs the benchmark against whatever is configured.
9. **Benchmark** (`semif/benchmark`). Items come from the eval corpus: claims, epochs (not a session's first prompt) and contract sentences. Rules and sensor are therefore measured on the same data. Per family it reports coverage, balanced accuracy on answered items, Brier score, 10-bin ECE, p50/p95 latency, and the rules' balanced accuracy.

## Consequences
- The `semif` flag is on by default, which is harmless because the default backend is `null`. Shadow logging starts as soon as a backend is enabled.
- M7's code exit criteria are met by tests with fake, raising, malformed, blocking, mock-HTTP and fake-encoder backends (`tests/test_semif.py`): 0 malformed results consumed, saturation never blocks a hook, and rules-only mode works with the null backend.
- The tier table itself is **not yet backed by measurements.** M7.2 (real llama.cpp runs) and the benchmark on the actual quantized files need the GPU and model downloads. Until then no family is routed to a model for any decision, only for shadow logging.
