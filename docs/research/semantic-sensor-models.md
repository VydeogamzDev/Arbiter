# Semantic sensor models: candidates, speed estimates, quality (2026-09-24)

Research note behind [decision 0026](../decisions/0026-sensor-backends-model-tiers-and-fine-tuning.md). Numbers marked *estimate* are derived, not measured. Replace them with the M7 sensor benchmark once the 3080 Ti is back.

## What the sensor does, and what speed matters

Arbiter's semantic sensor never writes text. It scores a few answer options after one pass over a prompt: direct-logit scoring, the SemIf technique (spec §4, §7). So:

- **Prompt-processing (prefill) speed decides throughput.** Generation speed barely matters.
- **Demand is small.** Roughly 1–5 judgments per second at peak. All scoring is asynchronous and never awaited in a hook (§4.4.5).
- **Shared-prefix reuse helps a lot.** A stable prompt prefix with a short per-question suffix gives about 3–5× more judgments per second (SemIf "shared state"; spec §7.5, R5).

Every GPU below clears the demand by a wide margin. **Choose the model by accuracy and VRAM, not speed.**

## Candidates

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

## Expected quality difference for Arbiter

- **General intelligence:** K2 7B is clearly stronger (+8 index points, about 1.6×).
- **Arbiter's short, reasoning-off option scoring:** the gap is probably smaller. The index rewards long reasoning and agentic work, and K2's recommended thinking mode (32k output tokens) would be switched off.
- **Working guess, to be replaced by the M7 benchmark:**
  - untuned, K2 is a few to about 10 accuracy points better on subtle judgments (scope change, relevance, claim nuance);
  - after fine-tuning both, a few points.

## Quantization and runtimes

- **GGUF Q4_K_M** is the default. 4-bit quantization shifts option logits slightly, so calibration and parity checks run on the exact file that ships (spec §22). Q5_K_M or Q6_K is preferred when VRAM allows.
- **EXL3 (exllamav3)** is usually faster than GGUF on NVIDIA at about 4 bits per weight. Before adopting it, confirm it supports Qwen3.5's hybrid layers and the new `k2_horizon` architecture.
- **Multi-LoRA serving:** llama.cpp and vLLM can apply different LoRA adapters per request. One base model in VRAM can then serve several fine-tuned decision families.

## Sources
- [Artificial Analysis — Qwen3.5 4B](https://artificialanalysis.ai/models/qwen3-5-4b)
- [Artificial Analysis — K2 Horizon 7B](https://artificialanalysis.ai/models/k2-horizon-7b)
- [Artificial Analysis — Intelligence Index v4.3.2](https://artificialanalysis.ai/evaluations/artificial-analysis-intelligence-index)
- [Artificial Analysis — Qwen3.5 small models](https://artificialanalysis.ai/articles/qwen3-5-small-models)
- [IFM/K2-Horizon-7B](https://huggingface.co/IFM/K2-Horizon-7B) · [GGUF files](https://huggingface.co/IFM/K2-Horizon-7B-GGUF/tree/main) · [IFM blog](https://ifm.ai/blog/k2/)
- [Qwen/Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B)
- [MindStudio — K2 Horizon tested locally](https://www.mindstudio.ai/blog/k2-horizon-local-models-tested)
- SemIf results: spec reference [S1] (TheoLeeCJ/SemIf)
