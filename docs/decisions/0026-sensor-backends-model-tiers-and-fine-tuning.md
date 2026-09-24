# 0026 — Semantic sensor: pluggable backends, VRAM model tiers, fine-tuned adapters

- Status: Accepted (plan for M7 and research track R5/R6; nothing here is built yet)
- Date: 2026-09-24
- Spec: §4, §7, §21, §22, §26; decisions 0009, 0010
- Research: [docs/research/semantic-sensor-models.md](../research/semantic-sensor-models.md)

## Context
- **The spec pairs one technique with one model.** It names SemIf as the sensor and Qwen3.5-4B BF16 as its model. SemIf is really a technique: read the option logits after one prefill.
- **Qwen3.5-4B's published accuracy is modest:** 0.813 balanced accuracy on SemIf's own set, 0.637 on WANLI.
- **A stronger model now exists.** K2 Horizon 7B (Apache 2.0, fully open) scores 21 on the Artificial Analysis Intelligence Index, vs 13 for Qwen3.5-4B. It fits in 8–12 GB at 4 bits.
- **Speed is not the bottleneck.** The sensor is asynchronous and needs roughly 1–5 judgments/s. Every current GPU clears that at Q4 (estimates in the research note).
- **Accuracy decides how much the sensor saves.** The cascade (§7.7) abstains when the sensor is unsure, and every abstention is a lost saving.

## Decision
1. **SemIf is one candidate behind a backend interface, not a fixed dependency.** Backends:
   - `null`: rules only, abstains; the default with no GPU.
   - `llama_cpp`: GGUF. This is the first real backend, with multi-LoRA and prefix caching.
   - `semif_bf16`: the SemIf reference path, kept for comparison and parity.
   - `exl3`: later, once it supports the chosen architectures.

   Every backend returns the same scored-option result and passes the same validation (§7.3).
2. **Model tiers by VRAM.** `arbiter semif enable` detects VRAM and proposes the largest tier that fits, with room left for context. It downloads only after confirmation.

   | VRAM | Default model | Quant |
   | --- | --- | --- |
   | 6 GB+ | Qwen3.5-4B | Q4_K_M (Q5_K_M/Q6_K at 8 GB) |
   | 12 GB+ | K2 Horizon 7B | Q4_K_M (Q5_K_M if headroom allows) |
   | 16 GB+ | K2 Horizon 7B | Q6_K |

   K2 Horizon 7B also runs at 8 GB with a short context (about 8–10k tokens). It's allowed there as an opt-in, not the default.

   The table is a starting point. It is adopted only if the sensor benchmark (item 5) confirms each tier beats the one below it by a meaningful margin at acceptable latency.
3. **Reasoning off.** Sensor calls use direct option scoring with thinking disabled. K2's recommended 32k-token thinking mode is for generation, not scoring.
4. **Fine-tuned LoRA adapters, one per decision family.** Examples: completion claim, scope change, context relevance, retrieval relevance, review risk. Adapters sit on the tier's base model, and one base stays in VRAM with adapters chosen per request. There are no separate full models per use case.
5. **Measure before adopting.** M7 adds a *sensor benchmark* to the eval corpus: labeled judgments per decision family, scored on balanced accuracy, calibration (Brier/ECE), abstention rate and latency, per model × quant × backend. Rules:
   - **Choose by accuracy, then VRAM.** Speed only has to clear the budget.
   - **Parity runs on the shipped artifact.** Calibration and parity checks run on the exact quantized file (§22).
6. **Fine-tuning pipeline** (research track R6, after v0.1 has produced real traces):
   1. Collect decision candidates from the event log. Privacy and redaction rules apply unchanged.
   2. Label from outcomes and user decisions (waivers, confirmations, scope changes). A sample goes through an **offline Opus 5.5 labeling pass**, whose cost is estimated and confirmed before each run.
   3. Train QLoRA adapters locally on the GPU.
   4. Evaluate on held-out repos and time windows. An adapter ships only if it beats the untuned base and the rules on its family, with no regression elsewhere.
   5. Retrain when upstream formats or models change; drift triggers re-validation.
7. **Authority is unchanged.** Better models never gain authority. The sensor still can't certify completion, widen permissions or block by itself (§7.6), and it fails open through the cascade and breakers.

## Consequences
- M7.2 now targets `llama_cpp` (GGUF) as the first real backend, instead of the SemIf BF16 path. SemIf BF16 remains as a reference.
- Config gains `semif.backend`, `semif.model_tiers` and `semif.adapters` (§26). These keys are inert until M7.
- The Qwen 4B vs K2 7B choice, and whether fine-tuning closes the gap, is settled by the benchmark, not by the Intelligence Index.
- K2 Horizon is three weeks old. Its tooling (parsers, EXL3 support) needs checking at M7 time.
