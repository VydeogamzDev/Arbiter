# Semantic sensor models: candidates, speed estimates, quality (2026-09-24, amended the same day)

Research note behind [decision 0026](../decisions/0026-sensor-backends-model-tiers-and-fine-tuning.md). Numbers marked *estimate* are derived, not measured. Replace them with the M7 sensor benchmark once the 3080 Ti is back.

## What the sensor does, and what speed matters

Arbiter's semantic sensor never writes text. It scores a few answer options after one pass over a prompt: direct-logit scoring, the SemIf technique (spec §4, §7). So:

- **Prompt-processing (prefill) speed decides throughput.** Generation speed barely matters.
- **Demand is small.** Roughly 1–5 judgments per second at peak. All scoring is asynchronous and never awaited in a hook (§4.4.5).
- **Shared-prefix reuse helps a lot.** A stable prompt prefix with a short per-question suffix gives about 3–5× more judgments per second (SemIf "shared state"; spec §7.5, R5).

Every GPU below clears the demand by a wide margin. **Choose the model by accuracy and VRAM, not speed.**

## The landscape (typed-decision models)

- **Jev** (TypeSafe): a commercial typed-decision model. JevBench is the public benchmark for this category; Jev 1.13.0 scores 63.29 on v1.4.
- **SemIf:** direct option-logit scoring on Qwen3.5-4B.
- **JevK5:** Qwen3.5-4B with a distilled LoRA, published merged (`alibiserikbay/JevK5`, BF16, 8.41 GB) and as GGUF (`alibiserikbay/JevK5-GGUF`: Q4_K_M 2.71 GB, Q8_0 4.48 GB, plus a 2B Q8_0), read out with SemIf's protocol (softmax over the answer letters' next-token logits, temperature T = 1.532). About 13 ms per decision on an H100 with CUDA graphs. Apache 2.0. Ranked second of 76 on JevBench v1.4 (62.04).
- **GLiNER2.5-Decide** (Fastino, released 2026-09-24):
  - a 340M DeBERTa-v3-large encoder, Apache 2.0;
  - handles single-label, multi-label, yes/no, 0–10 score, and questions over a passage;
  - runs locally on CPU; full fine-tuning through the `gliner2` trainer;
  - trained domains include **agent completion**, routing, handoff, moderation, severity and urgency;
  - no reasoning or open-ended answers; English examples only; no max input length or calibration metrics published.

| Model | Size | Fast Decisions (Fastino's suite, 17 domains × 300 held-out) |
| --- | --- | --- |
| GLiNER2.5-Decide | 0.34B | 60.2% |
| GLiNER2 XL | 1B | 59.6% |
| JevK5 | 4B | 57.6% |
| SemIf (Qwen3.5-4B) | 4B | 56.4% |
| GLiFormer large-v1 | — | 49.0% |
| Laya Router | 0.4B | 46.6% |

How to read these results:
- **The lead is probably real on this suite.** With about 5,100 examples, the standard error is around 0.7 points, so +2.6 over JevK5 is more than noise.
- **It is a vendor suite** whose domains resemble GLiNER's training data. Independent testing (Hanno-Labs decision-bench) was only just requested.
- **All systems score 56–60%.** None is an oracle, and the Arbiter-specific sensor benchmark decides.

What each stage suits:

| Arbiter judgment | Encoder (GLiNER2.5-Decide) | Decoder (JevK5 / K2 7B) |
| --- | --- | --- |
| completion claim in a final message | strong fit (short text, trained domain) | works, overkill |
| scope change vs "continue" | strong fit (short text) | works |
| requirement detection / contract coverage | good fit after fine-tuning | works |
| context or retrieval relevance, review risk, effort choice | weak: long inputs (code, diffs, state), trained on 512-token-class inputs, no reasoning | intended use |

## Candidates (decoder tiers)

| | Qwen3.5-4B | K2 Horizon 7B |
| --- | --- | --- |
| Artificial Analysis Intelligence Index v4.3.2 (reasoning variants) | **13** | **21** |
| Parameters | 4.7B total (~4.2B text; includes a vision tower) | ~9B total. "7B" is the non-embedding core: 36 layers, hidden 4096, vocab 250,624, untied embeddings; BF16 file 18 GB |
| Attention | hybrid: 3 Gated DeltaNet layers per 1 full-attention layer (8 of 32 layers keep a KV cache) | standard GQA (32 heads, 8 KV heads, head dim 128) on all 36 layers |
| KV cache (FP16) | ~32 KB/token (~0.26 GB at 8k) | ~147 KB/token (~1.2 GB at 8k, ~4.7 GB at 32k) |
| Q4_K_M GGUF | ~2.7 GB (*estimate*) | 5.59 GB (official IFM GGUF; Q5_K_M 6.47, Q6_K 7.39, Q8_0 9.57 GB) |
| Context | 262k | 524k |
| License / openness | Apache 2.0, open weights | Apache 2.0, open weights, data, code and recipe (released 2026-09-03) |
| Vendor claims | — | beats Qwen3.5-9B on IFM's own tables (e.g. SWE-bench Verified 70.6 vs 50.8, HLE 18.6 vs 14.9, AA-LCR 68.0 vs 65.3) |
| Known rough edges | — | Early independent test: vLLM tool parser failed; weak from-scratch web task; garbled low-resource languages. Irrelevant to option scoring, but the tooling is young. |

The SemIf repository reports, for Qwen3.5-4B:
- **Accuracy:** 0.813 balanced accuracy on its authored decision set, 0.637 on WANLI.
- **Speed on an RTX 3090:** 2.33 fresh decisions/s (BF16, Hugging Face transformers).

## Speed estimates (Q4_K_M, llama.cpp, one request at a time)

*Estimates*, ±30–40%. How they were derived:
- **Generation:** memory bandwidth × efficiency ÷ bytes read per token.
- **Prefill:** scaled from typical llama.cpp int8 matmul throughput for 7B-class models.

Qwen's numbers are less certain, because llama.cpp's Gated DeltaNet kernels are newer. "Judgments/s" assumes a fresh 1,000-token prompt; prefix reuse multiplies it by about 3–5×.

| GPU (VRAM) | Qwen 4B gen tok/s | K2 7B gen tok/s | Qwen 4B prefill tok/s | K2 7B prefill tok/s | Judgments/s (Qwen / K2) |
| --- | --- | --- | --- | --- | --- |
| RTX 4060 (8 GB) | ~70 | ~40 | ~3,000 | ~2,000 | ~3 / ~2 |
| RTX 3070 (8 GB) | ~110 | ~65 | ~4,000 | ~2,700 | ~4 / ~2.7 |
| RTX 3060 (12 GB) | ~90 | ~50 | ~2,500 | ~1,700 | ~2.5 / ~1.7 |
| RTX 4070 (12 GB) | ~125 | ~70 | ~5,800 | ~3,900 | ~6 / ~4 |
| RTX 5070 (12 GB) | ~170 | ~95 | ~6,200 | ~4,100 | ~6 / ~4 |
| **RTX 3080 Ti (12 GB)** | **~230** | **~130** | **~6,900** | **~4,600** | **~7 / ~4.6** |
| RTX 4060 Ti (16 GB) | ~70 | ~40 | ~4,400 | ~3,000 | ~4.4 / ~3 |
| RTX 4080 (16 GB) | ~180 | ~100 | ~9,800 | ~6,500 | ~10 / ~6.5 |
| RTX 5080 (16 GB) | ~240 | ~135 | ~11,000 | ~7,500 | ~11 / ~7.5 |
| RTX 3090 (24 GB) | ~235 | ~130 | ~7,100 | ~4,700 | ~7 / ~4.7 |
| RTX 4090 (24 GB) | ~250 | ~140 | ~16,500 | ~11,000 | ~16 / ~11 |
| RTX 5090 (32 GB) | ~400 | ~250 | ~21,000 | ~14,000 | ~21 / ~14 |

Artificial Analysis's 25.8 tok/s figure for Qwen3.5 4B is an API provider's generation speed, not a local one.

## Tier 0 speed and footprint

These are *estimates* for a 340M encoder (DeBERTa-v3-large-class).

| Where it runs | Footprint | Short decision (≤ 256 tokens) |
| --- | --- | --- |
| Modern desktop CPU | ~2 GB RAM (the published weights are FP32, 1.95 GB; FP16/INT8 ONNX: less) | ~20–60 ms |
| Any recent NVIDIA GPU | ~1 GB VRAM in FP16 (2 GB FP32) | ~3–10 ms, batches well |

That's far above demand, and it needs no GPU. That's why tier 0 can ship before the decoder backend.

## Expected quality difference for Arbiter

- **General intelligence:** K2 7B is clearly stronger (+8 index points, about 1.6×).
- **Arbiter's short, reasoning-off option scoring:** the gap is probably smaller. The index rewards long reasoning and agentic work, and K2's recommended thinking mode (32k output tokens) would be switched off.
- **Working guess, to be replaced by the M7 benchmark:**
  - untuned, K2 is a few to about 10 accuracy points better on subtle judgments (scope change, relevance, claim nuance);
  - after fine-tuning both, a few points.

## Measured results (2026-09-24, CPU, eval corpus)

These are the first real runs of `semif/benchmark.py`: 120 labeled items from the eval corpus, both option orders per binary question, no fine-tuning or calibration. Both runs were on the development machine's CPU (the 3080 Ti was in repair). The "rules" column is the deterministic baseline on the same items.

| Family (items) | Rules | GLiNER2.5-Decide: coverage / bal. acc. / ECE | JevK5 4B Q4_K_M: coverage / bal. acc. / ECE |
| --- | --- | --- | --- |
| completion_claim (60) | 1.00 | 90% / 1.00 / 0.20 | 87% / 0.97 / 0.17 |
| scope_change (20) | 1.00 | 65% / 0.76 / 0.19 | 65% / 1.00 / 0.09 |
| requirement_detection (40) | 0.83 | 45% / 0.75 / 0.44 | 43% / 0.57 / 0.49 |
| p95 latency per judgment (2 variants) | <1 ms | ~0.8 s (CPU) | ~4.4 s (CPU; GPU expected ~50–100 ms) |

- **Neither model beats the rules on any family yet**, so nothing is routed to the sensor (`semif.route_families` stays empty) and both stay shadow-only. That's the M7 exit rule working as intended.
- **The corpus flatters the rules.** It was written alongside them, and they score 1.0 on two families. A fair comparison needs a held-out corpus of phrasings the rules weren't tuned on: paraphrased claims, implicit scope changes, requirements stated indirectly.
- **What looks promising:** JevK5 is well calibrated on scope change (ECE 0.09) and both models are accurate on completion claims when they answer. Requirement detection is weak zero-shot for both. It's the first candidate for a fine-tuned adapter (research track R6).
- **Runtime notes:**
  - JevK5 is a reasoning model. With a raw prompt it opens `<think>` (letter mass 0), so the backend renders the model's chat template with `enable_thinking: false`.
  - Scores must be pre-sampling: llama.cpp's post-sampling probabilities at temperature 0 are 1.0 for the greedy token.
  - The published GLiNER2.5-Decide weights are FP32 (1.95 GB). They load in about 6–10 s on CPU.

## Quantization and runtimes

- **GGUF Q4_K_M** is the default. 4-bit quantization shifts option logits slightly, so calibration and parity checks run on the exact file that ships (spec §22). Q5_K_M or Q6_K is preferred when VRAM allows.
- **EXL3 (exllamav3)** is usually faster than GGUF on NVIDIA at about 4 bits per weight. Before adopting it, confirm it supports Qwen3.5's hybrid layers and the new `k2_horizon` architecture.
- **Multi-LoRA serving:** llama.cpp and vLLM can apply different LoRA adapters per request. One base model in VRAM can then serve several fine-tuned decision families.

## Sources
- [fastino/GLiNER2.5-Decide](https://huggingface.co/fastino/GLiNER2.5-Decide) · [Fastino models](https://fastino.ai/models) · [GLiNER2.5 blog](https://fastino.ai/blog/gliner2-5-span-free-information-extraction)
- [Hanno-Labs decision-bench intake for GLiNER2.5-Decide](https://github.com/Hanno-Labs/decision-bench/issues/27)
- [JevK5](https://github.com/ngrok-adhoc/jevk5) · [allebee/jevk5](https://github.com/allebee/jevk5) · [alibiserikbay/JevK5](https://huggingface.co/alibiserikbay/JevK5)
- [Artificial Analysis — Qwen3.5 4B](https://artificialanalysis.ai/models/qwen3-5-4b)
- [Artificial Analysis — K2 Horizon 7B](https://artificialanalysis.ai/models/k2-horizon-7b)
- [Artificial Analysis — Intelligence Index v4.3.2](https://artificialanalysis.ai/evaluations/artificial-analysis-intelligence-index)
- [Artificial Analysis — Qwen3.5 small models](https://artificialanalysis.ai/articles/qwen3-5-small-models)
- [IFM/K2-Horizon-7B](https://huggingface.co/IFM/K2-Horizon-7B) · [GGUF files](https://huggingface.co/IFM/K2-Horizon-7B-GGUF/tree/main) · [IFM blog](https://ifm.ai/blog/k2/)
- [Qwen/Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B)
- [MindStudio — K2 Horizon tested locally](https://www.mindstudio.ai/blog/k2-horizon-local-models-tested)
- SemIf results: spec reference [S1] (TheoLeeCJ/SemIf)
