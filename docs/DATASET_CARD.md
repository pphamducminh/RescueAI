# Dataset card

**Status:** A 100-record synthetic SOS **development sample** is included. Its schema and design distributions were checked automatically; Vietnamese human review, a release license, and extractor evaluation remain pending. No road dataset or real SOS records are included.

## Synthetic SOS development sample

| Field | Recorded value |
| --- | --- |
| Name | RescueAI synthetic Vietnamese SOS benchmark v1, development sample |
| Origin | Offline template/rule generator; every record has `synthetic=true` |
| Size and split | 100 JSONL records; 60 train, 20 dev, 20 test |
| Master seed | `412073` |
| Files | [`sample_100.jsonl`](../experiments/sos_benchmark/sample_100.jsonl), [`sample_100.summary.json`](../experiments/sos_benchmark/sample_100.summary.json), [`sample_100.sha256`](../experiments/sos_benchmark/sample_100.sha256) |
| JSONL SHA-256 | `0d18d5dc53f33cd5fe7537e9a34b448ddbbd2fffca86387b1368a19545d9ca7b` |
| Generation environment | Python 3.13.7, Pydantic 2.13.5; exact source and phrase-bank hashes are stored in each record |
| Location and time | Synthetic coordinates and generic landmarks; fixed reproducible 2026-09-28 reference time, not observations |
| Human audit | Pending for all 100 records |
| License for separate dataset release | Pending; do not infer one from this card |

Each private record separates `ground_truth` (latent incident, pre-render communication plan, and gold annotation) from `generated_input` (SOS Schema v1 input). Gold is based on reported claims and supplied incident-role metadata, not on concealed incident truth. The sample has all eleven SOS fields with explicit unknowns and exact Unicode text evidence spans. It is text only. The `test` split is a held-out template-family development partition, **not** an independently audited final benchmark.

The design targets and realized distributions are recorded in the summary file. They are stress-test quotas, not estimates of real disaster incidence. The [generator documentation](../evaluation/sos_benchmark/README.md) gives the regeneration command and limits. No model has been scored on this sample.

## Optional Task 6 linguistic variants

The [natural-language variation pipeline](../evaluation/sos_benchmark/natural_variants/README.md) can export report-visible render specs from the frozen Task 5 sample, then import externally produced Vietnamese paraphrases into separate raw, accepted, rejected, and review JSONL files under `data/sos_benchmark/natural_variants/`. This is an optional variation layer, not a new source of gold labels. Its provider-facing payload excludes stored submitted GPS context; hidden latent facts are not generation inputs. The repository does not include a paid API adapter or claim that an external LLM generated or semantically verified variants.

Passing deterministic checks only puts a candidate in `NEEDS_SEMANTIC_REVIEW` unless an explicitly supplied reviewer approves it. Before any candidate enters a frozen evaluation set, document its source and prompt, independently verify that it preserves the planned claims and omissions, re-anchor source-text evidence spans, and complete Vietnamese human review. Variant counts, checksums, and review outcomes should be added to this card only after those artifacts exist and are audited.

## Future datasets

Create one completed card per additional dataset or scenario collection before publishing results. Clearly label generated scenarios **synthetic**. Do not present synthetic reports or road networks as real disaster observations.

## Dataset identity

| Field | Value to record |
| --- | --- |
| Name and version | _Pending_ |
| Owner or source | _Pending_ |
| Source URL or collection method | _Pending_ |
| License and permitted uses | _Pending_ |
| Collection or generation dates | _Pending_ |
| Geographic and time coverage | _Pending_ |
| Real, synthetic, or mixed | _Pending_ |
| File paths and checksums | _Pending_ |

## Contents and preparation

Record the number of SOS reports, teams, road nodes and edges, road events, and scenarios where applicable. Describe the schema, units, missing fields, deduplication, filtering, de-identification, and any synthetic generation rules or random seeds. Define the meaning of team capacity and the completion rule used to count a served SOS.

For extraction evaluation, document who labeled each field, annotation guidance, disagreement resolution, train/validation/test splits, and scoring rules. Do not report precision, recall, or F1 without a reviewed labeled set. Preserve source records and raw per-scenario experiment outputs where their use is permitted.

## Intended use and limitations

**MUST HAVE for evaluation:** reproducible, clearly sourced comparison of FCFS, nearest feasible team, and RescueAI policies on identical scenario inputs. Document known gaps in coverage, data quality, realism, and potential bias. Synthetic scenarios can test software behavior but cannot establish real-world rescue effectiveness.

**NICE TO HAVE:** a licensed small real road network and sensitivity analysis. **POST-COMPETITION:** real-time operational data feeds. Neither is present in this scaffold.

Before using any real SOS content, document consent or lawful basis, privacy handling, access restrictions, retention, and whether public release is allowed. Leave unresolved items marked _Pending_ instead of assuming permission.
