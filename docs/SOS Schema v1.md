# RescueAI — SOS data contract v1

**Status:** design proposal for the competition MVP, 2026-09-28. This is a decision-support data contract, not a clinical triage protocol. All examples below are **synthetic**. A dispatcher approves any operational assignment.

## 1. Boundary and field semantics

The four layers are immutable snapshots linked by `sos_id` and revision:

1. **`SOSInput`** preserves the original report and submitted metadata.
2. **`ExtractedSOS`** records evidence-backed statements and uncertainty. It does not turn a reported claim into a verified event.
3. **`ValidatedSOS`** records a dispatcher's field-by-field review, including unresolved fields. “Accepted as reported” means the dispatcher has accepted that report for planning; it is **not** external confirmation of its truth.
4. **`PriorityAssessment`** records a versioned, explainable *recommendation* computed from an extracted or reviewed snapshot. It is separate from facts and from assignment approval.

Use UTC timestamps with offsets. `received_at` is assigned by the server and is the FCFS ordering timestamp. `reported_event_at` may be absent, because a person may not know when the event began. Never use a phone's time or the time an image was uploaded as proof of when a pictured event occurred.

**Implementation scope:** **MUST HAVE:** all four contracts, text evidence, explicit unknown/conflict states, field review, location-role checks, and versioned rule-based priority. **NICE TO HAVE:** extracting visual evidence from optional images, confidence calibration, and more detailed location uncertainty. **POST-COMPETITION:** trusted live responder/sensor feeds, operational integrations, and clinical triage protocols designed and validated with emergency professionals. The contract can represent future sources without claiming that these integrations exist in the MVP.

### One wrapper for every extracted field

`EvidenceField[T]` is required for every fact, including unknown facts:

| `state` | `value` | `candidates` | Meaning |
| --- | --- | --- | --- |
| `supported` | A value of type `T` | At least one evidence-backed candidate | The source supports **a claim**, not established truth. `false`, `[]`, and `0` are allowed only when explicitly supported. |
| `uncertain` | `null` | One or more candidates, possibly unparsed | Hedged or vague claim; retain the original passage. |
| `unknown` | `null` | Empty | No usable evidence; set `unknown_reason` to `not_mentioned` or `unreadable`. |
| `conflicting` | `null` | Two or more contradictory candidates | Preserve both claims and their evidence; never select a winner silently. |

Each `Candidate[T]` stores `value: T | null`, `assertion: stated | hedged`, `evidence_ids: nonempty list[str]`, and `confidence: float | null`. `EvidenceField` also stores `confidence: float | null` for a supported value, `unknown_reason` when relevant, and `candidates` for provenance. `confidence` refers only to **extraction correctness**, never to the probability that a victim survives or that a report is truthful. Do not require an LLM to invent a number; store `null` until a meaningful calibrated score exists. For `uncertain`, `unknown`, or `conflicting`, the field-level confidence is `null`.

`Evidence` identifies the exact origin: `source_type` (`sos_text`, `sos_image`, `submitted_gps`, `dispatcher_input`, `reporter_followup`, `responder_observation`, or `sensor_data`), `source_ref`, optional exact `text_span`, optional normalized `image_box`, and optional `captured_at`. `text_span` uses zero-based Python Unicode-string offsets `[start,end)` into the **unchanged** original message. An image observation must have an image reference and region; an extracted textual claim must point to an exact text span. Distinguish `method` on each candidate (`ai_text`, `ai_image`, `direct_input`, `human`, `sensor`). A submitted GPS point is direct input, not an AI prediction.

## 2. Fact inventory, with deliberately narrow enums

| Field in `ExtractedSOS.facts` | Value type | Semantics |
| --- | --- | --- |
| `people_count` | `CountRange {min: int >= 0, max: int >= min | null}` | People **reported at the incident**. `max=null` means “at least min”; never turn “vài người” into an invented range. |
| `injury_reported` | `bool` | `false` only for an explicit denial of injuries. |
| `urgent_signs` | list of `unconscious_reported`, `breathing_difficulty_reported`, `severe_bleeding_reported`, `other_urgent_symptom_reported` | Symptoms **reported in words** or confirmed by a qualified observer; an image alone cannot establish unresponsiveness or a diagnosis. `[]` only if the source explicitly denies urgent symptoms. |
| `vulnerable_groups` | list of `{group, count: CountRange | null}` | Groups explicitly mentioned: `child`, `older_adult`, `pregnant_person`, `disability_reported`, `limited_mobility_reported`, `other_reported`. Missing groups are not implicitly absent; a known list need not be exhaustive. |
| `trapped` | `bool` | Whether inability to leave is reported explicitly. |
| `water_signals` | list of `flooding_reported`, `water_rising_reported`, `strong_current_reported` | Multiple tags can coexist. `[]` requires an explicit denial of relevant water conditions. |
| `fire_signals` | list of `fire_reported`, `flames_observed`, `smoke_observed`, `spreading_reported` | Smoke alone does **not** prove flames; these are reported or visible signals. |
| `structure_signals` | list of `damage_reported`, `cracks_reported`, `collapse_reported` | Do not convert these into a structural safety judgment. |
| `access_observations` | list of `{mode, status}` | `mode`: `vehicle`, `foot`, `boat`, `unspecified`; `status`: `passable_reported`, `difficult_reported`, `blocked_reported`. Vehicle blockage does not imply blockage on foot or by boat. |
| `requested_assistance` | list of `boat`, `ambulance`, `medical_team`, `evacuation`, `other_reported` | What the reporter **requests**, not a verified resource requirement. |
| `incident_location` | `LocationCandidate` | `description: str | null`, `point: GeoPoint | null`, `precision: point | approximate_area | landmark`, with provenance. In extraction, a point can only be copied from an explicit incident GPS/pin; later human-reviewed geocoding belongs in `ValidatedSOS`. The extractor must never invent coordinates. |

For positive tag lists, `supported` means the listed tags were observed **at least**; absence of an unlisted tag is not a negative claim. Set `supported, value=[]` only for an explicit denial of **the entire category**. If a narrower denial is given (“không có lửa” but smoke is seen), preserve the distinct claims and avoid collapsing them into `fire_signals=[]`. Contradictions about the same location and time require `conflicting`.

`SOSInput.submitted_location` is `{point, role, source, accuracy_m, captured_at}` where `role` is `incident | reporter | unspecified`, `source` is `device_gps | map_pin | typed_coordinates`, and unknown `accuracy_m`/`captured_at` are `null`. A phone's GPS is often the **reporter** location; an unspecified role must not be assumed to be the incident. `GeoPoint` has latitude `[-90,90]` and longitude `[-180,180]`; `accuracy_m` is an instrument-provided uncertainty when available, not a model guess.

`ValidatedSOS.location_resolution.quality` is `externally_confirmed_point | reported_point | approximate_area | landmark_only | unresolved | conflicting`. This refers to the **incident**, not merely the GPS quality. It separately records `routing_anchor: {graph_node_id, snapped_distance_m, accepted_by, accepted_at} | null`. A report is routeable only when a dispatcher has accepted an incident point and graph anchor; an area, landmark, unclear reporter GPS, or unresolved conflict must not produce a precise rescue route. A reported point stays labeled `reported_point` even after a dispatcher accepts it for preliminary planning. Matching a point to a graph node is a routing operation with its own recorded distance, not an LLM inference.

## 3. Four models

### A. `SOSInput`

- Required: `schema_version`, `sos_id`, `input_revision` (starts at 1), nonempty `raw_text`, `received_at` (server assigned).
- Nullable, explicitly present: `image_ref` (internal reference, not the image bytes), `submitted_location`, `reported_event_at`.
- Original text remains unchanged. Images and person-identifying contact data, if later needed, have separate access controls; a contact number and a person's name are **not** priority features.

### B. `ExtractedSOS`

- Required: `schema_version`, `extraction_id`, `sos_id`, `input_revision`, `extractor` (`kind: ai | rule_based`, `model_id: str | null`, `version`, `prompt_version: str | null`, `modalities_used`), `extracted_at`, `evidence`, and all eleven `facts` above.
- If AI is unavailable, the offline fallback is explicitly `kind=rule_based`; never include its output in an AI extractor's reported accuracy.
- Evidence and candidates preserve hedged statements, negations, contradictions, and which image/text/GPS was used. A field can be `unknown` even when the report contains other useful facts.

### C. `ValidatedSOS`

- Required: `schema_version`, `validation_id`, `sos_id`, `extraction_id`, `revision`, `fields`, `location_resolution`, `overall_review_state: partial | complete`, and `updated_at`.
- Every fact has a `FieldResolution[T]` with `review_state: accepted_as_reported | corrected_by_dispatcher | externally_confirmed | unresolved | not_reviewed`, `value: T | null`, `evidence_ids`, `reviewer_id: str | null`, `reviewed_at: datetime | null`, and `reason: str | null`. Only the first three states permit non-null `value`; they require a reviewer and time. The remaining states require `value=null`.
- `fields` contains the same eleven typed keys as `ExtractedSOS.facts`; do not silently inherit a model value when a field has not been reviewed. Corrections are append-only review events, so an earlier extraction can always be inspected.
- `location_resolution` includes the incident point or description if resolved, the quality, and the optional reviewed routing anchor. An approved route/assignment is stored in the dispatch workflow, **not** in this model.

### D. `PriorityAssessment`

- Required: `schema_version`, `assessment_id`, `sos_id`, `based_on: {stage: extracted | validated, snapshot_id}`, `policy_version`, `assessed_at`, `suggested_attention: immediate_review | elevated_review | standard_review | insufficient_information`, `priority_weight: number | null`, `reasons`, `unresolved_fields`, `requires_human_review: bool`. `snapshot_id` points to an immutable `extraction_id` or `validation_id` matching `stage`.
- A `reason` names `field_path`, `rule_id`, a human-readable explanation, and optional numeric contribution. A weight can be emitted **only** under a documented deterministic, versioned rule. No rule/insufficient evidence → `priority_weight=null`, `suggested_attention=insufficient_information`, and a request for human review. The exact formula and operating thresholds remain an **open project decision**; none is implied by this contract.
- Assessments based on `extracted` are provisional and say so in the UI. A new reviewed fact creates a **new assessment**. Approval or rejection of a proposed rescue assignment is a separate audited action.

## 4. Synthetic examples

The table displays relevant `ExtractedSOS.facts` in compact notation: `S(x)` means `state=supported,value=x`; `U` means explicit `state=unknown,value=null`; `?(x)` means `state=uncertain,value=null,candidates=[x]`; `C(x,y)` means `state=conflicting,value=null,candidates=[x,y]`. **Every omitted fact is explicitly `U` in a complete record.** Each supported or candidate value carries its own evidence ID; these are illustrative inputs, not model evaluation results. All location descriptions and GPS points are synthetic.

| Case and original Vietnamese SOS | Relevant extracted facts | Location handling |
| --- | --- | --- |
| **Ordinary:** “Chúng tôi có 2 người tại điểm ghim. Không ai bị thương hay mắc kẹt. Đường bộ vào được.” | `people_count=S([2,2])`; `injury_reported=S(false)`; `trapped=S(false)`; `access_observations=S([{mode:unspecified,status:passable_reported}])`; `urgent_signs=U`; `vulnerable_groups=U`; all hazard categories `U`. | A map pin explicitly labeled **incident** can become `incident_location=S(point)`; the dispatcher still chooses/accepts a graph anchor. |
| **Potentially critical:** “Có 5 người trên mái nhà, 2 trẻ nhỏ, không thể rời đi. Nước đang dâng; một người bất tỉnh. Xe không vào được, cần xuồng.” | `people_count=S([5,5])`; `vulnerable_groups=S([{child,[2,2]}])`; `trapped=S(true)`; `water_signals=S([water_rising_reported])`; `urgent_signs=S([unconscious_reported])`; `access_observations=S([{vehicle,blocked_reported}])`; `requested_assistance=S([boat])`; `injury_reported=U`. | Without a usable incident location, show a review alert; do not fabricate an ETA despite serious reported signs. |
| **Ambiguous:** “Bên kia cầu có vài người, hình như một người ngất. GPS này là chỗ tôi đang đứng.” | `people_count=?(null)` with quote “vài người”; `urgent_signs=?([unconscious_reported])` with hedged source; `injury_reported=U`; `trapped=U`. | Submitted GPS has `role=reporter`; `incident_location=U`, `location_resolution.quality=unresolved`; ask for an incident pin or a dispatcher-selected point. |
| **Conflict:** “Tin đầu nói có 2 người trong căn nhà này; hàng xóm lại nói có 5 người **trong chính căn nhà đó**. Một người bảo đang cháy, người kia bảo không cháy.” | `people_count=C([2,2],[5,5])`; `fire_signals=C([fire_reported],[])`. The competing candidates have separate exact text spans. | If a pin is explicitly incident-tagged, it may still be usable for location; the people/fire fields remain unresolved until reviewed. |

For the conflict example, a concrete field fragment is:

```json
{
  "people_count": {
    "state": "conflicting",
    "value": null,
    "confidence": null,
    "unknown_reason": null,
    "candidates": [
      {"value": {"min": 2, "max": 2}, "assertion": "stated", "confidence": null, "method": "ai_text", "evidence_ids": ["text_1"]},
      {"value": {"min": 5, "max": 5}, "assertion": "stated", "confidence": null, "method": "ai_text", "evidence_ids": ["text_2"]}
    ]
  }
}
```

The dispatcher could later accept one of these reports, mark the count unresolved, or confirm a different count through a follow-up. None is automatic.

## 5. Extraction evaluation contract

Create a held-out, human-labeled dataset of **original** texts and optional images, with normalized field values, states (`supported/uncertain/unknown/conflicting`), source spans, and independent incident-location roles. Keep synthetic examples explicitly labeled; do not claim field performance without this dataset and a scoring script. Resolve annotator disagreements and document ambiguous labels. Keep reporters and follow-ups out of both train and held-out sets when they are the same incident to avoid leakage.

- For binary fields (`injury_reported`, `trapped`): report positive-class precision/recall/F1, negative accuracy, a four-state confusion matrix, and coverage/abstention. A missed explicit `false` is distinct from `unknown`.
- For tag arrays: per-tag and macro/micro precision/recall/F1. Compare only what the text claims; a missing tag is not a proven negative.
- For `people_count`: exact-range match and boundary error on labeled numeric cases; separately count vague/unknown and conflict detection. Do not pretend that an invented integer for “vài người” is correct.
- For locations: incident-versus-reporter role classification, quality-state confusion matrix, and coordinate error **only** where a reference incident coordinate exists.
- For evidence: span/region grounding accuracy and the fraction of supported facts with valid source references. Report calibration/Brier/ECE only if a meaningful numeric confidence has been trained/calibrated against labels; no numeric confidence is required for MVP.
- Evaluate AI and offline rule-based outputs in **separate** cohorts. Maintain dataset version, split, model/prompt version, labeling rubric, and raw per-example predictions.

## 6. Values an LLM must not assert directly

- Clinical diagnosis, death, chance of survival, clinical triage category, or the absence of medical danger just because symptoms were not mentioned.
- Pregnancy, disability, exact age, injury count, identity, credibility, or exact number of people **from appearance alone**; an image may support a labeled observation only when clear, with its source region and uncertainty.
- Precise incident coordinates from a vague landmark; treating the sender's device GPS as the victims' location without an explicit location role.
- Road passability, safe water depth, building stability, current on-scene conditions, or confirmed resource suitability from an old image or unverified SOS message. Dynamic road/availability data belong to their own separately sourced graph and team records.
- A final priority, rescue-team assignment, route safety guarantee, or authorization to dispatch. The versioned policy computes suggestions and the human dispatcher approves operational action.

This separation reflects WHO's call for expert supervision and rigorous evaluation when LLMs are used for health-related decision support: https://www.who.int/news/item/16-05-2023-who-calls-for-safe-and-ethical-ai-for-health .

## 7. Concise JSON Schema-style contract for Codex

The following is a **type contract**, not a literal runnable JSON Schema: `Field<T>` and `Resolution<T>` are generic notation, and cross-field rules below require validators. Codex should implement Pydantic models, then export the actual JSON Schema using `model_json_schema()`.

```text
schema_version = "1.0"
Time = ISO-8601 datetime with timezone offset
GeoPoint = {lat: float[-90..90], lon: float[-180..180]}
CountRange = {min: int>=0, max: int>=min | null}
UrgentSign = "unconscious_reported"|"breathing_difficulty_reported"|
             "severe_bleeding_reported"|"other_urgent_symptom_reported"
VulnerableGroup = {group: "child"|"older_adult"|"pregnant_person"|
                   "disability_reported"|"limited_mobility_reported"|"other_reported",
                   count: CountRange|null}
WaterTag = "flooding_reported"|"water_rising_reported"|"strong_current_reported"
FireTag = "fire_reported"|"flames_observed"|"smoke_observed"|"spreading_reported"
StructureTag = "damage_reported"|"cracks_reported"|"collapse_reported"
AccessObservation = {mode: "vehicle"|"foot"|"boat"|"unspecified",
                     status: "passable_reported"|"difficult_reported"|"blocked_reported"}
AssistanceTag = "boat"|"ambulance"|"medical_team"|"evacuation"|"other_reported"
Candidate<T> = {value: T | null, assertion: "stated"|"hedged",
                method: "ai_text"|"ai_image"|"direct_input"|"human"|"sensor",
                evidence_ids: [str, ...], confidence: float[0..1] | null}
Field<T> = {state: "supported"|"uncertain"|"unknown"|"conflicting",
            value: T | null, candidates: Candidate<T>[],
            confidence: float[0..1] | null,
            unknown_reason: "not_mentioned"|"unreadable"|"vague" | null}
Evidence = {id: str, source_type: "sos_text"|"sos_image"|"submitted_gps"|
            "dispatcher_input"|"reporter_followup"|"responder_observation"|"sensor_data",
            source_ref: str, text_span: {start:int,end:int,quote:str} | null,
            image_box: {x:float,y:float,w:float,h:float} | null,
            captured_at: Time | null}
SubmittedLocation = {point: GeoPoint, role: "incident"|"reporter"|"unspecified",
                     source: "device_gps"|"map_pin"|"typed_coordinates",
                     accuracy_m: float>=0 | null, captured_at: Time | null}
LocationCandidate = {description: str | null, point: GeoPoint | null,
                     precision: "point"|"approximate_area"|"landmark"}
ExtractorInfo = {kind: "ai"|"rule_based", model_id: str|null,
                 version: str, prompt_version: str|null,
                 modalities_used: list<"text"|"image"|"gps">}
LocationQuality = "externally_confirmed_point"|"reported_point"|
                  "approximate_area"|"landmark_only"|"unresolved"|"conflicting"
RoutingAnchor = {graph_node_id: str, snapped_distance_m: float>=0,
                 accepted_by: str, accepted_at: Time}
Reason = {field_path: str, rule_id: str, explanation: str,
          contribution: float|null}
Facts = {
  people_count: Field<CountRange>, injury_reported: Field<bool>,
  urgent_signs: Field<list<UrgentSign>>, vulnerable_groups: Field<list<VulnerableGroup>>,
  trapped: Field<bool>, water_signals: Field<list<WaterTag>>,
  fire_signals: Field<list<FireTag>>, structure_signals: Field<list<StructureTag>>,
  access_observations: Field<list<AccessObservation>>,
  requested_assistance: Field<list<AssistanceTag>>,
  incident_location: Field<LocationCandidate>
}
A SOSInput = {schema_version, sos_id: str, input_revision: int>=1,
              raw_text: nonempty str,
              image_ref: str|null, submitted_location: SubmittedLocation|null,
              received_at: Time, reported_event_at: Time|null}
B ExtractedSOS = {schema_version, extraction_id: str, sos_id: str,
                  input_revision: int>=1, extractor: ExtractorInfo,
                  extracted_at: Time, evidence: Evidence[], facts: Facts}
C ValidatedSOS = {schema_version, validation_id: str, sos_id: str, extraction_id: str,
                  revision: int>=1, fields: map<each Facts key, Resolution<T>>,
                  location_resolution: {quality: LocationQuality,
                    point: GeoPoint|null, description: str|null,
                    routing_anchor: RoutingAnchor|null},
                  overall_review_state: "partial"|"complete", updated_at: Time}
Resolution<T> = {review_state: "accepted_as_reported"|"corrected_by_dispatcher"|
                "externally_confirmed"|"unresolved"|"not_reviewed",
                value: T|null, evidence_ids: str[], reviewer_id: str|null,
                reviewed_at: Time|null, reason: str|null}
D PriorityAssessment = {schema_version, assessment_id: str, sos_id: str,
                        based_on: {stage:"extracted"|"validated", snapshot_id:str},
                        policy_version: str, assessed_at: Time,
                        suggested_attention: "immediate_review"|"elevated_review"|
                          "standard_review"|"insufficient_information",
                        priority_weight: float>0|null, reasons: Reason[],
                        unresolved_fields: str[], requires_human_review: bool}
```

**Required validator invariants:** require every field key even when unknown; `supported` requires non-null value and supporting evidence; `unknown` requires null value, zero candidates, and a reason; `uncertain` requires null value and evidence-backed candidate(s); `conflicting` requires null value and at least two contradictory candidates; non-supported field-level confidence is null; `CountRange.max >= min` when max exists; `false`, `[]`, and `0` require direct explicit support; evidence IDs must exist and text spans/regions must match source; `LocationCandidate.precision=point` requires a point while landmarks/areas must not masquerade as a point; coordinates and timestamps must be bounded/timezone-aware; reviewed non-null values need reviewer/time and provenance; `complete` review has no `not_reviewed` fields; a routing anchor requires a dispatcher-accepted incident point; an assessment based on extracted data is provisional. Set `extra='forbid'` and validate these rules with Pydantic `model_validator` rather than relying on type hints alone. Keep exact enum strings stable across API, labeled datasets, and experiment versions.

Pydantic supports discriminated unions and JSON Schema export; JSON Schema treats omitted and explicit `null` as different states. References: https://pydantic.dev/docs/validation/latest/concepts/unions/ ; https://pydantic.dev/docs/validation/latest/concepts/json_schema/ ; https://json-schema.org/understanding-json-schema/reference/object .
