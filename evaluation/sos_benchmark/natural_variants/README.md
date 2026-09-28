# SOS natural-language variants (Task 6)

Task 5 is the primary, deterministic, offline synthetic SOS benchmark. It samples a latent scenario, freezes a communication plan and gold annotation, then renders Vietnamese text. Task 6 is an **optional linguistic variation layer** for those frozen cases. It prepares provider-independent instructions, imports candidate paraphrases, and triages them. Task 6 never changes Task 5 ground truth, labels, splits, or source records. No GPT, Gemini, Claude, paid API, or other external generation call is implemented here.

## Export render specifications

From the repository root:

```sh
.venv/bin/python -m evaluation.sos_benchmark.natural_variants export \
  --cases experiments/sos_benchmark/sample_100.jsonl --limit 10
```

The default output is `data/sos_benchmark/natural_variants/render_specs.jsonl`. The input is the private Task 5 JSONL, which is validated before export. `build_render_spec(case)` reads the frozen `communication_plan`, `gold_annotation`, and submitted location metadata. It does **not** read `latent_scenario`, generation metadata, or the original raw message to select facts. Source quotes come from validated gold text evidence. The case split selects only its vetted, irrelevant phrase bank when that noise type is permitted.

Each stored `RenderSpec` has these top-level fields:

| Field | Meaning |
| --- | --- |
| `case_id` | ID of the frozen Task 5 case. |
| `allowed_facts` | Report-visible facts keyed by SOS field. Each retains `state`, `value`, and claims with assertion, witness/scope/time identifiers, and original evidence quotes. Supported `false` and zero remain explicit values; uncertain and conflicting facts retain their claim values instead of being resolved. |
| `must_remain_unknown` | Omitted SOS fields that must not gain a stated value in a variant. |
| `external_context` | Submitted location metadata and any GPS-only incident-location fact held outside text generation. This is retained for local validation, **not** for the provider prompt. |
| `allowed_distractors` | Vetted irrelevant phrases permitted by the frozen communication plan. |
| `style` | Vietnamese (`language: "vi"`) and the plan's preferred noise flags. |

The stored JSONL includes `external_context` for local inspection; the importer rebuilds specs from the frozen Task 5 cases for GPS-leakage checks. **Do not send the full stored spec to a language-generation provider.** Use `RenderSpec.provider_payload()` as the provider-facing contract; it omits `external_context`. Keep the private Task 5 records and exported specs under the same data access controls. Hidden latent facts must never be included in a prompt, adapter argument, or provider log. The domain schema file is [`docs/SOS Schema v1.md`](../../../docs/SOS%20Schema%20v1.md).

## Import candidate variants

An external process may return one JSON object per line in a local input file:

```json
{"case_id":"syn-v1-000001","source_id":"optional-batch-id","variants":[{"text":"...","noise_type":"paraphrase","variant_id":"optional-variant-id"}]}
```

`source_id` and `variant_id` are optional. Run the offline importer with an existing file:

```sh
.venv/bin/python -m evaluation.sos_benchmark.natural_variants import \
  --cases experiments/sos_benchmark/sample_100.jsonl \
  --input external.jsonl
```

The importer writes the following files under `data/sos_benchmark/natural_variants/`:

| File | Contents |
| --- | --- |
| `render_specs.jsonl` | Exported instructions for frozen cases. |
| `generated_raw.jsonl` | Byte-for-byte copy of the imported file for audit; it can contain invalid JSON if the input did. |
| `accepted.jsonl` | Candidates approved by an explicitly supplied semantic reviewer after deterministic checks. |
| `rejected.jsonl` | Candidates with detectable failures, invalid batches, or an explicit semantic rejection, with reasons. |
| `needs_review.jsonl` | Candidates without a detected hard failure that still require independent semantic review. |

The CLI has no semantic reviewer attached. Therefore a candidate that passes deterministic checks goes to `NEEDS_SEMANTIC_REVIEW`, and the CLI does not automatically populate `accepted.jsonl`. Code can inject a future `SemanticValidator` into `import_variants`; only its explicit `APPROVED` decision permits `ACCEPTED`. These statuses describe Task 6 triage, not Task 5 ground-truth quality.

## What the offline checks establish

The importer validates case IDs, JSON/schema shape, non-empty text, duplicate text/source/variant IDs, required count values, unexpected numeric tokens, obvious unknown-field cues, detectable false-versus-unknown errors, GPS coordinates, and recognizable unauthorized distractors. It retains conflicts and hedges in the render spec; cues that are not mechanically verifiable remain for semantic review.

These rules are intentionally conservative but incomplete. They cannot prove that a paraphrase preserves every relation, speaker attribution, negation, time reference, or location meaning. A reviewer must compare every candidate against its render spec and the frozen Task 5 communication plan. Before any variant joins a **frozen evaluated benchmark**, independently validate its meaning, re-anchor evidence spans in the new text, and complete the dataset's human audit. Original Task 5 evidence offsets refer to the original text and must not be reused for a variant.

The 100-record Task 5 sample and any Task 6 variants remain synthetic development material. The sample is unaudited; no external LLM output, reviewer approval, or extractor performance is claimed by this documentation. See the [benchmark overview](../README.md), [methodology](../../../docs/Synthetic%20SOS%20Benchmark%20v1.md), and [dataset card](../../../docs/DATASET_CARD.md).
