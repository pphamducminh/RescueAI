# AI usage and disclosure

This is a disclosure template for the RescueAI competition project. The local demo uses a disclosed rule-based SOS extractor and priority suggestion; it does not contain an SOS extraction model, image model, or evaluated AI feature.

## Intended AI role

**MUST HAVE for the completed MVP:** AI-assisted extraction of structured information from SOS text, with the original report retained and missing or uncertain fields shown to a dispatcher. The system will offer an explainable suggested priority. A dispatcher reviews recommendations and makes the final operational decision.

**NICE TO HAVE:** analysis of attached images. **POST-COMPETITION:** live automated integrations or operational deployment. These are plans, not implemented capabilities.

The local demo uses a rule-based extractor to work offline. Its outputs are labeled **rule-based** and must not be counted as AI extraction results.

## Usage record

Record each actual model or assistant use before making a competition disclosure. Do not fill unknown cells with guesses.

| Date | Purpose | Tool/model and version | Inputs or dataset | Prompt or method reference | Human review | Output or evidence |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-09-28 | Repository architecture scaffold, documentation, and tests | OpenAI Codex (GPT-6; deployment identifier not recorded) | User request, `PROJECT_CONTEXT.md`, `docs/MVP Architecture v1.md`, and existing repository files | This development conversation; full prompt transcript is not stored in this repository | Human team review pending | Files in this scaffold and local check output |
| 2026-09-28 | SOS schema models, validation, synthetic test fixtures | OpenAI Codex (GPT-6; deployment identifier not recorded) | User request, `docs/SOS Schema v1.md`, and repository code | This development conversation; full prompt transcript is not stored in this repository | Human team review pending | `backend/domain/sos*.py` and `tests/test_sos_models.py`; no LLM integration |
| 2026-09-28 | Offline synthetic SOS benchmark generator, documentation, and tests | OpenAI Codex (GPT-6; deployment identifier not recorded) | User request, `docs/Synthetic SOS Benchmark v1.md`, SOS domain schemas, and repository code | This development conversation; full prompt transcript is not stored in this repository | Vietnamese human review of generated sample pending | `evaluation/sos_benchmark/`, `tests/test_sos_benchmark.py`, and 100 synthetic sample records; the generator itself makes no external LLM call |
| 2026-09-28 | Provider-independent SOS variation pipeline, documentation, and tests | OpenAI Codex (GPT-6; deployment identifier not recorded) | User's Task 6 request, frozen Task 5 sample, benchmark code, and repository documentation | This development conversation; full prompt transcript is not stored in this repository | Independent semantic review of any imported variants pending | `evaluation/sos_benchmark/natural_variants/` and its tests; the pipeline has no external generation-provider integration |
| 2026-09-29 | Local competition demo integration, offline extraction rules, dashboard, API, tests, and launch documentation | OpenAI Codex (GPT-6; deployment identifier not recorded) | User request, existing architecture and domain contracts, and repository code | This development conversation; full prompt transcript is not stored in this repository | Human team review pending | `backend/demo/`, `backend/extraction/rules.py`, `backend/priority/rules.py`, `frontend/src/`, `tests/test_demo_*.py`, and `docs/DEMO.md`; no SOS LLM is called |

The team should retain or export the exact prompt transcript before a competition
submission if its rules require prompt logs. This row does not substitute for that
record or for human review.

For development assistance, record generated code, documentation, tests, and human edits. For SOS extraction experiments, record the model identifier, settings, exact prompt version, schema version, fallback behavior, and which dataset records were evaluated. Keep prompt logs under version control when permitted, without private credentials or real personal data.

## Evidence and limits

- Evaluate extraction only on a reviewed labeled set with a stated scoring method. Report precision, recall, and F1 only after running that evaluation.
- Keep extracted claims separate from reviewed facts. Preserve uncertainty, source text, and fields requiring verification.
- Do not use an uncertain extraction as a medical diagnosis or an autonomous dispatch order.
- Document failed or ambiguous outputs and any manual corrections. State when an output came from a rule-based fallback.
- Never invent model performance, citations, prompt logs, or real-world rescue outcomes.
- For Task 6 paraphrases, pass only `RenderSpec.provider_payload()` to a generation model; the stored `external_context` and hidden Task 5 latent scenario must stay out of prompts and provider logs. Record the actual provider, model/version, prompt, settings, and per-variant review if such a model is used later. Deterministic triage alone cannot certify semantic equivalence; re-anchor evidence spans after review.

See [MVP Architecture v1](MVP%20Architecture%20v1.md) for the target pipeline and [Dataset card](DATASET_CARD.md) for dataset provenance requirements.
