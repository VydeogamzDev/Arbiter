# 0026 — Semantic sensor: pluggable backends, an encoder tier 0, VRAM decoder tiers, fine-tuned adapters

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
- **Amended the same day** after two findings:
  - Fastino released **GLiNER2.5-Decide**, a 340M Apache-2.0 typed-decision encoder that runs on CPU. On Fastino's own 17-domain suite it scores 60.2%, vs 57.6% for JevK5 and 56.4% for SemIf; "agent completion" is one of its trained domains.
  - **JevK5** turned out to be Qwen3.5-4B with a distilled LoRA (published merged, with its own GGUF builds) and a calibrated temperature, using SemIf's scoring method: an already-tuned version of the 6 GB tier model.

## Decision
1. **SemIf is one candidate behind a backend interface, not a fixed dependency.** Backends:
   - `null`: rules only, abstains; the default with no GPU.
   - `encoder`: GLiNER2.5-Decide and similar typed-decision encoders, on CPU or GPU. This is tier 0.
   - `llama_cpp`: GGUF. This is the first real backend, with multi-LoRA and prefix caching.
   - `semif_bf16`: the SemIf reference path, kept for comparison and parity.
   - `exl3`: later, once it supports the chosen architectures.

   Every backend returns the same scored-option result and passes the same validation (§7.3).
2. **Two stages: an encoder tier 0, then decoder tiers by VRAM.** `arbiter semif enable` detects the hardware, proposes the tiers that fit (with room left for context), and downloads only after confirmation.

   | Tier | Hardware | Default model | Handles |
   | --- | --- | --- | --- |
   | 0 | any (CPU fine; 1.95 GB FP32 weights) | GLiNER2.5-Decide (340M encoder) | short, high-volume typed decisions: completion claim, scope change / continuation, requirement detection, contract coverage |
   | 1 | 6 GB+ VRAM | JevK5 (Qwen3.5-4B, distilled LoRA merged), Q4_K_M 2.71 GB (Q8_0 4.48 GB at 8 GB) | long-context and reasoning-heavy judgments |
   | 2 | 12 GB+ VRAM | K2 Horizon 7B, Q4_K_M (Q5_K_M if headroom allows) | the same, stronger |
   | 2+ | 16 GB+ VRAM | K2 Horizon 7B, Q6_K | the same, closer to BF16 logits |

   - **Tier 0 needs no GPU,** so it can ship before the decoder backend (M7.2) and before the 3080 Ti returns.
   - **Routing:** each decision family is sent to exactly one stage, based on benchmark results. The encoder is trained on 512-token-class inputs, so longer inputs go to the decoder or are summarized deterministically first.
   - **JevK5 on llama.cpp:** the published merged GGUF (`alibiserikbay/JevK5-GGUF`) is used directly; no separate base model or adapter is needed. Its calibration temperature is re-fit on the quantized file.

   K2 Horizon 7B also runs at 8 GB with a short context (about 8–10k tokens). It's allowed there as an opt-in, not the default.

   The table is a starting point. It is adopted only if the sensor benchmark (item 5) confirms each tier beats the one below it by a meaningful margin at acceptable latency. For a given family, tier 0 has to beat both the rules and tier 1.
2b. **Claim detection stays rules-first.** Tier 0 may only *add* completion claims the rules missed, which widens what the gate checks. It never removes a rule-detected claim, and a model can never mark anything verified. Its confidence scores are calibrated before the cascade uses them; the model card publishes no calibration metrics.
3. **Reasoning off.** Sensor calls use direct option scoring with thinking disabled. K2's recommended 32k-token thinking mode is for generation, not scoring.
4. **Fine-tuned adapters, one per decision family.** Tier 0 families are fine-tuned directly: a 340M encoder trains in minutes on the 3080 Ti with Fastino's `gliner2` trainer. Examples: completion claim, scope change, context relevance, retrieval relevance, review risk. Adapters sit on the tier's base model, and one base stays in VRAM with adapters chosen per request. There are no separate full models per use case.
5. **Measure before adopting.** M7 adds a *sensor benchmark* to the eval corpus: labeled judgments per decision family, scored on balanced accuracy, calibration (Brier/ECE), abstention rate and latency, per model × quant × backend. Rules:
   - **Choose by accuracy, then VRAM.** Speed only has to clear the budget.
   - **Parity runs on the shipped artifact.** Calibration and parity checks run on the exact quantized file (§22).
6. **Fine-tuning pipeline** (research track R6, after v0.1 has produced real traces):
   1. Collect decision candidates from the event log. Privacy and redaction rules apply unchanged.
   2. Label from outcomes and user decisions (waivers, confirmations, scope changes). A sample goes through an **offline Opus 5.5 labeling pass**, whose cost is estimated and confirmed before each run.
   3. Train locally on the GPU: full fine-tunes of the tier 0 encoder, and QLoRA adapters on the decoder base models.
   4. Evaluate on held-out repos and time windows. An adapter ships only if it beats the untuned base and the rules on its family, with no regression elsewhere.
   5. Retrain when upstream formats or models change; drift triggers re-validation.
7. **Authority is unchanged.** Better models never gain authority. The sensor still can't certify completion, widen permissions or block by itself (§7.6), and it fails open through the cascade and breakers.

## Consequences
- **M7.1b adds the `encoder` backend (tier 0).** It needs no GPU and may ship in a v0.2 before the decoder work.
- **M7.2 targets `llama_cpp` (GGUF) as the first decoder backend,** instead of the SemIf BF16 path. SemIf BF16 remains as a reference.
- **The GLiNER2.5-Decide numbers are vendor-reported, on a vendor suite** whose domains resemble its training data. Independent results (Hanno-Labs decision-bench) were pending at the time of writing. JevBench v1.4 ranks JevK5 v0.2 second of 76 systems (62.04, vs Jev 1.13.0 at 63.29).
- Config gains `semif.backend`, `semif.encoder`, `semif.model_tiers` and `semif.adapters` (§26). These keys are inert until M7.
- The Qwen 4B vs K2 7B choice, and whether fine-tuning closes the gap, is settled by the benchmark, not by the Intelligence Index.
- K2 Horizon is three weeks old. Its tooling (parsers, EXL3 support) needs checking at M7 time.
