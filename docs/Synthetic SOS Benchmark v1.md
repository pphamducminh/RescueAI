# RescueAI — reproducible synthetic Vietnamese SOS benchmark v1

**Status:** methodology proposal, 2026-09-28. No dataset has been generated or evaluated by this document. Every planned record is synthetic. The target is a **text extraction** benchmark compatible with *SOS Schema v1*, not an estimate of field performance in real disasters or a clinical triage benchmark.

## 1. Benchmark methodology

### 1.1 The two kinds of truth

Sample a structured `latent_scenario` first: the simulated incident state, including number of people, injuries, hazards, location role and time. Then sample a `communication_plan`: what this particular reporter knows and actually says; which facts are omitted, hedged, negated, or attributed to different witnesses. Render a Vietnamese message from that plan using versioned human-authored clauses and templates. Compile `gold_annotation` **from the render trace**, never from an extractor or an LLM's interpretation of the resulting sentence. Finally have people audit the text and gold while blind to extractor predictions.

The extractor is scored against **what the message and supplied metadata support**, not against the concealed incident state. For example, if the latent incident includes an injury but the text never mentions injury, `gold_annotation.facts.injury_reported.state=unknown`. For two incompatible reported counts, the text gold is `conflicting` even when the simulator knows the concealed count. The latent scenario is retained for reproducibility and other simulation work but never shown to an evaluated extractor or substituted for extraction labels.

Use *SOS Schema v1* states (`supported`, `uncertain`, `unknown`, `conflicting`), types, enums and evidence spans. A gold field has `state`, `value`, `unknown_reason` and evidence-backed `claims`; model `confidence` and model `method` are **not** part of gold. An explicit denial is `supported(false)` or `supported([])` where its scope really covers the field. Silence is `unknown`, not a negative. “Hình như một người ngất” is `uncertain` with a hedged candidate. “Vài người” is an uncertain count with no invented numeric range. Contradictions must concern the same place, incident, time and attribute; a clearly newer correction is not necessarily a conflict.

### 1.2 How to generate natural text without contaminating gold

- Write 30–50 **families** of short, plausible message frames and independently authored phrase banks for each split. Examples of phrasing: “Nhà em còn ba người ở cuối hẻm”, “Nước lên nhanh quá, xe không vào được”, “Có ai gần cầu nhỏ qua giúp với ạ”. Use ordinary Vietnamese, brief fragments, mild typos and occasional irrelevant details. Avoid repeating `SOS`, all-capitals, exclamation marks, or rigid “số người / nguy cơ / tọa độ” checklists as label shortcuts.
- A typed clause carries `field_path`, normalized value, assertion (`stated` or `hedged`), witness/scope/time and its local character span. Omit clauses for missing facts. For contradictions, render two distinct witness claims with incompatible values; never modify ground truth after rendering to suit the text.
- Apply only vetted surface changes: punctuation/spacing, a limited typo dictionary, colloquial alternatives, clause order, short filler and irrelevant information. **Protect** negations, quantities, danger words, witness attribution, place relations and temporal markers. Any edit that can change meaning needs a new communication plan and gold label, or it is rejected.
- Build final text from annotated segments; recompute exact zero-based Unicode code-point offsets in the final string. Do not search for the first matching phrase afterward, since a phrase may appear twice. Keep the original Vietnamese text unchanged for scoring.
- MVP uses no LLM to generate gold or messages. Optional LLM paraphrases are separate, human reviewed variants: reject or human reannotate any semantic drift, keep paired variants in one split, and never treat the LLM response as a label.
- MVP is text only (`image_ref=null`). An image track is NICE TO HAVE and requires controlled source images with separately checked visible evidence; generated imagery cannot establish a concealed medical fact or current road safety.

### 1.3 Sampling targets, not claims about real incidence

Set `N=1,500` for v1 (`900` train, `300` dev, `300` test); make the generator accept any `N` from about `500` to `2,000` and use deterministic quota rounding. The percentages below are **design quotas for a stress test**, not estimated Vietnamese disaster frequencies. Store both the latent scenario bucket and the final **message-visible** gold bucket; enforce the balancing targets on the latter so omitted danger signs do not accidentally create a misleading “high severity” label. These are *reported signal load* buckets, not medical severity or survival labels.

For reproducibility, define the message-visible bucket by a frozen, descriptive rubric: **high cue load** if a clearly reported urgent sign is present, or trapped people plus an active hazard, or at least two distinct active hazard categories; **intermediate** if it is not high but at least one hazard, explicit trapped status, or a reported access blockage is present; **lower** otherwise. Hedged, unknown and conflicting fields do not silently count as confirmed cues; retain them as separate review strata. This rubric is only for sampling and subgroup reports, never for deciding rescue priority.

| Sampling axis | Proposed target over message-visible gold | Reason |
| --- | --- | --- |
| Reported signal load | `30%` lower cue load; `35%` intermediate; `35%` high cue load | Ensure examples with combinations of reported urgent signs, trapped status and active hazards. The bucket is only a benchmark stratum. |
| Primary challenge type | `35%` ordinary; `25%` colloquial/noisy; `15%` deliberately missing important fields; `15%` vague/hedged; `10%` contradictory witnesses | Mutually exclusive *primary* assignment for quotas; secondary flags can overlap. |
| People count *as mentioned* | `55%` exact; `10%` numeric bounded/“at least”; `15%` vague; `15%` not mentioned; `5%` conflicting | Exercise values, ranges, abstention and conflict. Conditional on a known exact count, mostly 1–5 people; include a smaller group of 6–20, with no implausible regular flood of very large counts. |
| Injury field | `30%` explicitly injured; `20%` explicitly no injury; `37%` unmentioned; `10%` hedged; `3%` conflicting | Keep `false`, `unknown` and uncertain statements distinct. `urgent_signs` is sampled separately; “unconscious” need not imply a diagnosed injury. |
| Vulnerable people | Around `25%` explicitly mention at least one group; most remaining cases omit it; a small subset explicitly denies the entire category or hedges | Within positives include children, older adults, limited mobility, and smaller pregnancy/disability reports **from text only**. Allow multiple groups and note counts only when stated. |
| Hazard cues (overlapping) | Approx. `55%` water-related; `8%` fire/smoke-related; `12%` structural damage-related | Enrich less common conditions for evaluation while keeping fire and structural cases plausible. A visible smoke cue never implies visible flames. |
| Incident location | `40%` explicit incident pin; `35%` textual landmark/area; `10%` reporter GPS only; `10%` absent; `5%` competing incident locations | Test location *role* and routability. Use fictional or generic local references, never an actual person's home address. |

Make factor sampling **conditional** to rule out nonsensical combinations but do not bake evaluation shortcuts into the text. For example, a young child mentioned in a home may co-occur with an older adult; vulnerability does not imply a particular injury or attention tier. Independently apply informal wording (`~40%`), minor typos (`~20%`), fragments (`~25%`), irrelevant details (`~25%`) and urgency words (`~25%`), allowing overlap but capping mutations so text stays understandable. Distribute these across every signal-load bucket. Tune **only the generator design and dev set**, freeze the test generation plan, and document any rejected examples.

### 1.4 Split and reproducibility

Partition `template_family_id`, phrase-bank families, reporter-style families and `incident_group_id` **before generating messages**: no family or paired paraphrase may cross train/dev/test. One workable plan is 45 authored frame families, allocated 27/9/9 across the 60/20/20 splits; independently write split-specific surface phrases rather than simply swapping numbers in the same sentence. Balance strata within each split. A stricter held-out-template test is the primary reported test result; a random row split may be shown only as a leakage diagnostic, never as the headline benchmark. Group-aware splitting is a standard way to prevent the same group appearing in different partitions. See scikit-learn's `GroupShuffleSplit` documentation: https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.GroupShuffleSplit.html .

Pin generator code, schema/rubric version, phrase-bank hashes, Python/dependency versions, PRNG implementation and master seed. Derive independent per-case/per-attempt seeds with a stable digest of `(master_seed, split, slot, attempt, stage)`; do not use Python's process-dependent built-in `hash()`. Serialize canonical UTF-8 JSONL with deterministic key order, Unicode preserved and fixed `\n`; publish SHA-256 checksums and exact regeneration commands. An evaluated model's version, prompt, decoding settings, raw outputs and retry policy must also be logged; dataset reproducibility alone does not make an external model API deterministic.

## 2. JSONL record format

One physical line is one UTF-8 JSON object. The full private record contains:

| Key | Contract |
| --- | --- |
| `case_id`, `synthetic`, `split` | Stable ID; `synthetic=true`; split enum `train/dev/test`. |
| `versions` | `schema`, `generator`, `rubric`, `phrase_bank` and optional Git commit. |
| `generation` | Master/case seed, `template_family_id`, `incident_group_id`, `paired_variant_group_id` (nullable), primary challenge, secondary style flags, message-visible signal bucket. |
| `latent_scenario` | Sampled incident truth for simulation; **never** pass to extractor and **never** use directly as text-extraction gold. |
| `communication_plan` | Intended mentions/omissions, hedges, witness claims, time/scope relationships and applied semantic-safe noise. |
| `sos_input` | Complete `SOSInput` instance, including unmodified `raw_text`, nullable image/location and timestamps. |
| `gold_annotation` | `facts` with all eleven SOS Schema v1 keys, each with a state even when unknown; `evidence` with exact spans; `annotation_origin="render_trace"` plus human audit status. No model confidence in gold. |
| `qc` | Automatic validation results, human-review disposition, rejection reason if kept in an internal reject log. Only accepted cases enter released splits. |

The following **single physical JSONL line** is a synthetic format example. It intentionally contains a latent injury that the reporter does not mention. The number `8` belongs to the alley description, not to `people_count`:

```jsonl
{"case_id":"syn-v1-000001","synthetic":true,"split":"test","versions":{"schema":"1.0","generator":"1.0.0","rubric":"1.0","phrase_bank":"pb-v1"},"generation":{"seed":412073,"template_family_id":"family_test_03","incident_group_id":"incident_000001","paired_variant_group_id":null,"primary_challenge":"ordinary","style_flags":[],"signal_bucket":"intermediate"},"latent_scenario":{"people_count":3,"injury_present":true,"water_rising":true,"location_kind":"landmark"},"communication_plan":{"mentioned_fields":["people_count","water_signals","incident_location"],"omitted_fields":["injury_reported"],"hedged_fields":[],"conflict_fields":[],"noise_edits":[]},"sos_input":{"schema_version":"1.0","sos_id":"syn-v1-000001","input_revision":1,"raw_text":"Ở cuối hẻm số 8 có ba người, nước đang lên, cứu giúp với ạ.","image_ref":null,"submitted_location":null,"received_at":"2026-09-28T04:00:00Z","reported_event_at":null},"gold_annotation":{"annotation_origin":"render_trace","facts":{"people_count":{"state":"supported","value":{"min":3,"max":3},"unknown_reason":null,"claims":[{"value":{"min":3,"max":3},"assertion":"stated","evidence_ids":["e_people"]}]},"injury_reported":{"state":"unknown","value":null,"unknown_reason":"not_mentioned","claims":[]},"urgent_signs":{"state":"unknown","value":null,"unknown_reason":"not_mentioned","claims":[]},"vulnerable_groups":{"state":"unknown","value":null,"unknown_reason":"not_mentioned","claims":[]},"trapped":{"state":"unknown","value":null,"unknown_reason":"not_mentioned","claims":[]},"water_signals":{"state":"supported","value":["water_rising_reported"],"unknown_reason":null,"claims":[{"value":["water_rising_reported"],"assertion":"stated","evidence_ids":["e_water"]}]},"fire_signals":{"state":"unknown","value":null,"unknown_reason":"not_mentioned","claims":[]},"structure_signals":{"state":"unknown","value":null,"unknown_reason":"not_mentioned","claims":[]},"access_observations":{"state":"unknown","value":null,"unknown_reason":"not_mentioned","claims":[]},"requested_assistance":{"state":"unknown","value":null,"unknown_reason":"not_mentioned","claims":[]},"incident_location":{"state":"supported","value":{"description":"cuối hẻm số 8","point":null,"precision":"landmark"},"unknown_reason":null,"claims":[{"value":{"description":"cuối hẻm số 8","point":null,"precision":"landmark"},"assertion":"stated","evidence_ids":["e_location"]}]}},"evidence":[{"id":"e_location","source_type":"sos_text","source_ref":"syn-v1-000001","text_span":{"start":2,"end":15,"quote":"cuối hẻm số 8"}},{"id":"e_people","source_type":"sos_text","source_ref":"syn-v1-000001","text_span":{"start":19,"end":27,"quote":"ba người"}},{"id":"e_water","source_type":"sos_text","source_ref":"syn-v1-000001","text_span":{"start":29,"end":42,"quote":"nước đang lên"}}],"human_audit":"accepted"},"qc":{"schema_valid":true,"trace_valid":true,"human_review":"accepted"}}
```

The example's `signal_bucket` is an illustrative generator stratum. Its exact mapping must be versioned in the rubric; do not mistake it for clinical severity. Keep `latent_scenario`, `communication_plan` and `gold_annotation` in a private evaluation file. The **model-facing export** contains only `case_id`, `sos_input` and any genuinely available image reference; never prompt the model with a gold label, latent truth, template ID or challenge tag.

## 3. Generation pseudocode

```python
def generate_dataset(config):
    assert 500 <= config.n <= 2000
    family_splits = allocate_disjoint_families(config.families, ratios=(0.6, 0.2, 0.2))
    quotas = deterministic_round(config.n, config.quotas_by_split)
    accepted = {"train": [], "dev": [], "test": []}

    for split in ("train", "dev", "test"):
        for slot in range(quotas[split]["total"]):
            for attempt in range(config.max_attempts):
                rng = stable_rng(config.master_seed, split, slot, attempt)
                target = choose_unfilled_stratum(quotas[split], rng)
                latent = sample_structured_scenario(target, rng)   # FIRST
                plan = sample_communication(latent, target, rng)
                family = sample_from(family_splits[split], rng)
                segments = render_annotated_vietnamese(plan, family, rng)
                segments = apply_semantic_safe_noise(segments, plan, rng)
                text, spans, trace = concatenate_and_reindex(segments)
                gold = compile_field_states_and_claims(trace, plan)
                case = build_record(latent, plan, text, spans, gold, split, rng)

                if (matches_remaining_quotas(case, quotas[split])
                    and validate_sos_schema_and_gold(case)
                    and validate_text_span_quotes(case)
                    and validate_claim_scopes(case)
                    and passes_no_leakage_checks(case, accepted)):
                    accepted[split].append(case)
                    debit_quotas(quotas[split], case)
                    break
            else:
                raise GenerationError(f"Cannot fill {split} slot {slot}; revise quotas")

    human_audit_and_adjudicate(accepted)
    freeze_canonical_jsonl_and_sha256(accepted, config)
    return accepted
```

Within `sample_structured_scenario`, use conditional tables so water, vehicle access and rescue requests can co-occur sensibly, without automatically implying one another. `sample_communication` is the only component that chooses to withhold, hedge, negate or contradict a sampled fact. `compile_field_states_and_claims` maps the actual emitted segments to gold deterministically. Record failed attempts and quota changes; never silently loosen a quota based on test-model performance.

## 4. Evaluation metrics

Always report `n` for each field and each slice; pair aggregate numbers with confusion matrices, examples of errors and uncertainty intervals. Scores are about **matching reported content**, not detecting real-world injuries or measuring rescue outcomes.

| Target | Primary metric | Essential companion checks |
| --- | --- | --- |
| Four-state extraction | Macro-F1 and confusion matrix for `supported/uncertain/unknown/conflicting` per field | `unknown` recall; conflict precision/recall; false-certainty rate = predicted `supported` when gold is `unknown/uncertain/conflicting`. |
| Binary explicit values (`injury_reported`, `trapped`) | Precision/recall/F1 for `true` and accuracy on explicit `false`, conditioned on *gold supported* | Overall exact `(state,value)` match so abstaining or overasserting cannot disappear from the score. |
| Multi-label facts | Micro- and macro-F1 of **mentioned** tags (`urgent_signs`, water/fire/structure, vulnerable groups, assistance) | Separate exact set match and per-tag support; lack of a mention is not proof that a hazard/person is absent in the world. |
| Access and location | Match `(mode,status)` pairs; incident-versus-reporter role accuracy; location-quality state confusion | Unsupported incident points, false routeable rate, and coordinate error only when an incident-coordinate gold exists. |
| Numeric facts | Exact count/range match; MAE for gold exact counts only; bound errors for labeled finite numeric ranges | Correct handling of `max=null` (“at least”), vague counts, omission and conflicting counts. Never calculate MAE after filling unknowns with zero. |
| Evidence grounding | Exact/overlap span match and proportion of asserted fields with a valid evidence reference | Verify offsets against original text and image references when applicable. |
| Calibration, only if meaningful probabilities exist | Brier score for a precisely defined binary event, e.g. “this proposed `(state,value)` is exactly correct”, plus a reliability diagram | ECE with a few adequately populated bins, confidence coverage, risk–coverage curves, and per-slice calibration. Calibrate on dev, report once on untouched test. If numeric confidence is not meaningful, record `null` and mark these metrics N/A. |

The Brier score is a proper scoring rule for probability predictions, but its value also reflects resolution and dataset uncertainty; ECE/reliability plots complement it. An oversampled benchmark cannot by itself establish calibration under an unknown real incident distribution. See scikit-learn's calibration guide: https://scikit-learn.org/stable/modules/calibration.html . Define `confidence` as *extraction correctness against the text gold*, never report truth probability or medical risk.

Primary slices: reported signal-load bucket, vulnerable-group mention, injury mention state, missing/hedged/conflicting cases, count expression, location role, noise/style, and template family. Report subgroup sample sizes; if a subgroup is too small, call it exploratory instead of presenting a stable F1. Bootstrap uncertainty by incident/template group where support permits. AI extractor and rule-based fallback are separate systems with separate result tables.

## 5. Quality-control and release procedure

1. **Automatic checks for every record:** validate all eleven fields and state/value invariants; ensure field-level claims cite existing evidence; match exact text substrings to stored Unicode spans; verify contradictions have the same target/time and at least two incompatible claims; check time-zone-aware timestamps, location role, no fake incident coordinate, and uniqueness of IDs.
2. **Language check:** reject repetitive templates, extreme typo piles, impossible geography, unnaturally detailed medical or rescue terminology, and number/negation confusion. Use invented or generic locations without personal contact data. Audit cases where an irrelevant house number could be misread as a victim count.
3. **Independent human review:** two Vietnamese-speaking reviewers inspect **all test cases**, blind to `latent_scenario`, `communication_plan` and model outputs; adjudicate disagreements with a frozen rubric. Also review all conflict/ambiguity cases and a random, precommitted sample of ordinary train/dev cases. Reject or document correction when the text no longer supports its generated gold; preserve original/revised versions in an internal log.
4. **Leakage checks:** family and incident IDs disjoint across splits; compare normalized text and character/token n-gram similarity to flag duplicates or near-duplicates for manual review; keep paired clean/noisy versions in one split. Do not expose private test labels to prompting, model selection or demo-specific fine-tuning.
5. **Reproduction and reporting:** generate twice in a clean environment and compare byte-level SHA-256 hashes; rerun schema and scoring scripts; freeze test set before trying models. Publish design distributions, actual realized distributions, QC rejection counts, split family counts, raw per-case outputs and scoring code. State plainly that all performance is on synthetic Vietnamese SOS messages.

This review step is particularly important because research on synthetic evaluation has documented that synthetic benchmarks can be valid while missing some of the difficulty of human-authored tasks; high-stakes synthetic text also needs qualitative review. References: https://aclanthology.org/2025.findings-emnlp.526/ and https://aclanthology.org/2025.emnlp-demos.35/ .

## 6. Recommended ablations and scope

| Ablation | What it tests | Keep fixed |
| --- | --- | --- |
| Clean vs paired informal/typo/noisy wording | Robustness to text surface changes | Same sampled scenario and explicitly unchanged reported claims; paired cases remain in one split. |
| Full text vs masked injury/count/location evidence | Correct abstention and separation of latent truth from visible gold | Scenario and other clauses. Recompute message gold after masking. |
| No conflicts vs explicit same-scope conflicts | Whether extractor preserves competing claims | Family, length and nonconflict cues as closely as possible. |
| Text-only vs text plus independently labeled image | Incremental value of vision **if** the image track exists | Same report and location input; human checks visible cues; no diagnosis from image. |
| AI extractor vs rule-based fallback | Actual value of AI extraction | Identical held-out records and schema; report them separately. |
| Confidence before vs after dev-only calibration | Whether confidence helps identify extraction errors | Frozen model predictions and untouched test. |
| Random row split vs held-out-template-family split | Size of template memorization effect | Same generator corpus; **report the grouped split as the primary result**. |
| Train on 500/1,000/1,500 examples | Sensitivity to synthetic training volume | Same fixed test and split-specific phrase banks. |

**MUST HAVE for MVP:** deterministic text generator, frozen split, complete text gold/evidence, evaluation script, automated checks and human audit of test. **NICE TO HAVE:** image track, dev-calibrated numerical confidence, optional human-reviewed paraphrases. **POST-COMPETITION:** properly consented real-report validation and operational assessment with emergency responders; synthetic results alone do not establish field performance or safety.
