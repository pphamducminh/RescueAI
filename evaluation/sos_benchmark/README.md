# Synthetic SOS benchmark generator

This package generates **synthetic Vietnamese, text-only SOS reports offline**. It does not call an LLM, extract information from real reports, score an extractor, or provide a rescue-priority decision. The checked-in 100-record file is a development sample; every record has `human_review=pending`.

## Pipeline and labels

For each slot, the generator derives independent SHA-256 seeds from the master seed, split, slot, attempt, and stage. It then:

1. Samples a structured `latent_scenario`, including a synthetic incident point, landmark, time, people count, injury, and hazards.
2. Freezes a `communication_plan` of reported claims, omissions, hedges, and same-scope witness conflicts. This plan is made before message text exists.
3. Renders clauses from 30 split-disjoint template families and split-specific phrase banks. Only unannotated frame text receives vetted typos and other safe surface changes.
4. Attaches Unicode code-point spans from the render trace to the preplanned claims. It never extracts labels from the final sentence.
5. Validates the eleven gold fields, evidence links, visible cue-load rubric, and split-group separation before distribution reporting.

The private JSONL record has separate top-level `ground_truth` and `generated_input` fields. `ground_truth` contains the concealed incident, communication plan, and gold annotation. `generated_input` is a complete SOS Schema v1 `SOSInput`. Omitted facts are `unknown`, a statement of `false` or zero is `supported`, and same-scope incompatible witness claims are `conflicting`. The message-visible `signal_bucket` is a sampling stratum, **not** a severity or dispatch recommendation. Gold has no model confidence or method.

Do not give `ground_truth`, generation metadata, split labels, or template identifiers to an extractor under evaluation. A dedicated model-facing export and extractor scoring tool are still TODOs.

The [Task 6 natural-language variation layer](natural_variants/README.md) is optional. It builds provider-independent render specs from frozen, report-visible Task 5 claims and imports candidate paraphrases without changing the gold labels. Its stored specs include a locally held `external_context`; callers must use `provider_payload()` to exclude that context from a language-generation prompt. Mechanically valid imports still require independent semantic review and new evidence spans before use in a frozen benchmark. No external generation provider is integrated.

## Reproduce the development sample

From the repository root, with the development environment installed:

```sh
.venv/bin/python -m evaluation.sos_benchmark.generator \
  --seed 412073 --count 100 \
  --output experiments/sos_benchmark/sample_100.jsonl
.venv/bin/python -m evaluation.sos_benchmark.summary \
  experiments/sos_benchmark/sample_100.jsonl --json \
  > experiments/sos_benchmark/sample_100.summary.json
cd experiments/sos_benchmark
shasum -a 256 sample_100.jsonl > sample_100.sha256
```

Expected JSONL SHA-256 in the recorded environment: `0d18d5dc53f33cd5fe7537e9a34b448ddbbd2fffca86387b1368a19545d9ca7b`. The sample was generated with Python 3.13.7 and Pydantic 2.13.5. Each record also stores exact source and phrase-bank hashes. Byte-identical reproduction is established for the same source and dependency versions; the project does not yet provide a lockfile for other machines.

`--count` accepts any positive integer for smoke tests. The approved methodology proposes 1,500 cases and a usual 500–2,000 case range. The 100-case file is intentionally smaller at the user's request. All generated records remain unaudited samples, including those in the `test` split.

The [methodology](../../docs/Synthetic%20SOS%20Benchmark%20v1.md) defines the intended human audit and future evaluation. The [sample dataset card](../../docs/DATASET_CARD.md) records its current limits.
