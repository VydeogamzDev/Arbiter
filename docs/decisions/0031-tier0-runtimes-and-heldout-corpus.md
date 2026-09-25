# 0031 — Tier 0 runtimes (ONNX, Core ML), one-pass mirroring, and a held-out sensor corpus

- Status: Accepted
- Date: 2026-09-25
- Spec: §7.2–§7.5, §7.8, §20
- Builds on: [0026](0026-sensor-backends-model-tiers-and-fine-tuning.md), [0029](0029-m7-sensor-service.md)

## Context
The first real CPU runs (0029) showed three problems:
- GLiNER2.5-Decide's published weights are FP32 (1.95 GB), and the PyTorch path needs about 2.4 GB of RAM and 0.4–0.8 s per judgment.
- Fluid Inference's Core ML conversion showed the model tolerates 8-bit weights with essentially no accuracy change, but Core ML only runs on Apple devices.
- The eval corpus was written alongside the rules, so it couldn't show where the rules fail.

## Decisions
1. **One shared input path** (`semif/gliner_inputs.py`). GLiNER2 classifies by encoding the schema and text, then scoring the embedding at each `[L]` label marker with a small MLP.
   - Every runtime uses the upstream processor to build token ids and marker positions, so each one sees the training-time input. Only the heavy encoder runs elsewhere.
   - A manual PyTorch pass reproduces `classify_text` exactly.
2. **One-pass mirroring.** GLiNER2 scores several heads over the same text in one encoder pass. Both option orders of a binary question are now two heads of one call, which halves tier 0 cost. Head names are part of the model input, so they're short and neutral (`q1`, `q2`).
3. **ONNX runtime** (`backends/encoder_onnx.py`, `semif/onnx_export.py`, `arbiter semif export-onnx`):
   - the graph is `(input_ids, attention_mask, markers) -> logits`, with the encoder plus classifier and a dynamic sequence length;
   - precisions are weight-only, so activations stay FP32. `w8e4` (default) has 8-bit matmul weights and a 4-bit embedding table; `w8` keeps FP32 embeddings; `fp16` is for GPUs; `fp32` is the reference;
   - **rejected:** dynamic int8 quantizes activations and moved probe scores by up to 0.22;
   - every export is checked against PyTorch before it's accepted. It's refused if any probe score moves more than 0.05 or a confident answer flips; flips on near-ties don't count. The result is written to the export's manifest;
   - the runtime still imports the `gliner2` processor, and therefore PyTorch, for tokenization. It doesn't load the model weights.
4. **Core ML runtime** (`backends/encoder_coreml.py`, macOS 14+). It follows Fluid Inference's published package contract: fixed-length buckets, up to 4 heads per call, int32 inputs, and marker indices and masks. It picks the smallest bucket that fits, uses compute units `ALL`, and renormalizes over the used label slots. It's tested against a stubbed package; a real Mac run is still pending.
5. **Runtime selection** (`semif.encoder.runtime: auto`): a folder holding an Arbiter ONNX manifest gets `onnx`; Core ML packages on macOS get `coreml`; anything else gets `torch`.
6. **Held-out corpus** (`eval/corpus/heldout/sensor_v1.yaml`):
   - 160 labeled items: 60 completion claims, 50 scope changes as previous/latest pairs, 50 requirement sentences;
   - frozen: nothing is ever tuned against it, and it's retired to v2 if that happens;
   - provenance: written by the same assistant that wrote the rules, without consulting the rule code. That's weaker than real sessions or another author;
   - `arbiter semif bench --heldout`.
7. **Cascade metric.** The benchmark now reports `cascade_balanced_accuracy`: the sensor decides when it answers, the rules decide otherwise. That's the question routing has to answer: is rules plus sensor better than rules alone?

## Results (CPU, 2026-09-25)
**Tier 0 runtimes** on the 120-item eval corpus. All three make identical decisions, 120/120, with the largest positive-score difference 0.015 for `w8e4`:

| Runtime | Peak RAM | p50 / p95 per judgment | Load |
| --- | --- | --- | --- |
| PyTorch FP32 (one-pass) | 2,428 MB | 421 / 475 ms | 12.3 s |
| ONNX `w8` | 1,520 MB | 256 / 323 ms | 9.3 s |
| ONNX `w8e4` | 1,078 MB | 256 / 333 ms | 8.9 s |

**Held-out corpus**, balanced accuracy. "Cascade" means sensor when confident, otherwise rules:

| Family | Rules | Encoder `w8e4` (coverage, acc., cascade) | JevK5 4B Q4_K_M (coverage, acc., cascade) |
| --- | --- | --- | --- |
| completion_claim | 0.55 | 93%, 1.00, **0.95** | 77%, 0.97, 0.75 |
| scope_change | 0.56 | 52%, 0.83, 0.82 | 86%, 1.00, **0.94** |
| requirement_detection | **0.90** | 42%, 0.61, 0.78 | 68%, 0.73, 0.80 |

- **The rules were overfit.** They scored 1.0 on the eval corpus and about 0.55 on held-out phrasings for claims and scope changes. Requirement detection generalizes (0.90).
- **Recommended routing:**
  - completion_claim goes to tier 0, the encoder;
  - scope_change goes to the decoder (JevK5, which is the best calibrated with Brier 0.02) when a GPU is available, and to the encoder otherwise;
  - requirement_detection stays with the rules.
- **Not applied yet.** `semif.route_families` stays empty and the sensor stays shadow-only until a second, independent held-out set confirms this: examples from real sessions (with the user's consent) or from another author. The cascade module only recommends in any case (0030).

## Consequences
- Tier 0 is practical on any machine without a GPU: about 1 GB of RAM and about 0.26 s per judgment on a desktop CPU, or about 15 ms on Apple silicon (Fluid Inference's measurement).
- The export path, not published weights, defines the ONNX model. Each export's manifest records its source revision and parity.
- Fine-tuning (R6) should start with requirement detection, the family where no model helps yet. The held-out set must never be used for training.
