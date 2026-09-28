# RescueAI — transparent priority recommendation v1

**Status:** research prototype design, 2026-09-28. This is a **queue attention recommendation**, not clinical triage, a probability of survival, an allocation of the value of human lives, or authority to dispatch. Every numerical coefficient below is an **illustrative configuration for synthetic experiments**, not clinically validated. A human dispatcher retains approval and override authority. The rules use only report-supported or human-reviewed SOS facts from *SOS Schema v1*; a model-generated severity category or free-form medical diagnosis is not a scoring input.

## 1. Separate the two questions

1. **How urgently should a coordinator inspect a report?** Compute `U_i(t)` from the report, its evidence/uncertainty and waiting time. The score is independent of rescue-team location.
2. **How can an appropriate team reach it?** Determine team capability/availability, current road feasibility and travel time `tau[k,i,t]` from the routing module. A blocked route is `unreachable`, not a large fake ETA and not a reason to mark the people as less urgent. A proposed assignment still requires human approval.

`access_observations` (e.g. vehicle access reportedly blocked) belongs to routing, team eligibility and a review alert. Do **not** subtract it from urgency: difficult access is not evidence that people are less important. A submitted GPS identified as the reporter's location is not an incident coordinate.

WHO's Interagency Integrated Triage Tool describes a **facility-based medical** triage protocol; this prototype must not market itself as that protocol or invent clinical acuity categories. WHO also emphasizes human autonomy and explainability in AI for health. Sources: https://www.who.int/tools/triage ; https://www.who.int/news/item/28-06-2021-who-issues-first-global-report-on-ai-in-health-and-six-guiding-principles-for-its-design-and-use .

## 2. Five normalized cues

Use `received_at` assigned by the server, not an image timestamp or an unverified event time. Make a snapshot at time `t` for every pending SOS. `supported(false)` and `supported([])` require explicit negative evidence; `unknown`, `uncertain`, `conflicting`, or an unreviewed field remain unresolved.

For each cue let its domain be `[0,1]`. A fully supported value fixes a number; an unresolved value gets a **range**. An extracted AI claim is provisional even when `state=supported`, and its raw uncalibrated confidence must never be converted to a risk probability.

| Symbol | From SOS Schema v1 | Supported mapping | Unknown or conflict |
| --- | --- | --- | --- |
| `M` reported medical cue | `urgent_signs`, `injury_reported` | `M=max(X, 0.5Y)`, with `X=1` for at least one explicitly reported urgent sign and `Y=1` for explicitly reported injury. Explicit denial of the respective entire category sets its flag to `0`. An urgent sign and an injury **do not add twice**. | Bound the unknown `X` or `Y` in `[0,1]`; e.g. injury reported and urgent signs unknown gives `M in [0.5,1]`. |
| `T` trapped | `trapped` | `1` for explicitly reported trapped; `0` for explicitly denied. | `[0,1]` |
| `H` environmental cue | `water_signals`, `fire_signals`, `structure_signals` | `1` when at least one supported positive hazard tag exists; `0` only if all three categories explicitly deny hazards. Count presence once, regardless of number of tags. | `[0,1]` if no positive tag is supported and any relevant category remains unresolved. |
| `V` vulnerable group mention | `vulnerable_groups` | `1` for at least one explicitly reported group; `0` only for an explicit complete absence of this category. Repeated mentions do not add points. | `[0,1]` |
| `N` reported people count | `people_count` | `g(n)=min(max(n-1,0)/9,1)` for a reported exact integer. Use the two endpoints for a supported range; `max=null` gives upper endpoint `1`. | `[0,1]` for unknown/uncertain/conflicting counts, pending review. |

`M=0.5` for injury is an **engineering distinction between a stated injury and a stated urgent sign**, not a claim about their clinical relative severity. `H=1` means a hazard **was reported**, not that a building or route has been verified unsafe. A confirmed `people_count=0` should trigger a consistency review rather than silently producing a rescue assignment.

### Normalization and illustrative coefficients

```text
B_i = 0.40 M_i + 0.25 T_i + 0.20 H_i + 0.10 V_i + 0.05 N_i
A_i(t) = lambda * max(0, t - received_at_i) / T_ref
U_i(t) = B_i + A_i(t)

M,T,H,V,N in [0,1];       0 <= B_i <= 1
T_ref = 30 minutes;       lambda = 0.20 per T_ref (illustrative)
```

| Coefficient | Suggested MVP value | Why this *example* is bounded |
| --- | ---: | --- |
| Reported medical cue | `0.40` | One cue per report; cannot be inflated by adding synonym tags. |
| Reported trapped status | `0.25` | Separate from medical language. |
| Reported environmental cue | `0.20` | One bounded cue across water/fire/structure. |
| Mention of vulnerable group | `0.10` | Presence only; never infer age/pregnancy/disability from appearance. |
| Number of people | `0.05` | Saturates at 10 people, so group size cannot grow without limit. |
| Waiting | `0.20` per 30 minutes | Explicitly trades off waiting against the bounded `B` score; independent of routing. |

The static coefficients are nonnegative and sum to 1, so `B` has a stable `[0,1]` scale across scenarios. `A` is deliberately **uncapped**; `U` may exceed 1 and must not be displayed as a percentage or probability. There is no per-batch min–max normalization: adding a new SOS must not rescale every older report. The 30-minute time scale and all coefficients are editable, recorded in `policy_version`, and must be stress-tested; they are neither a medical threshold nor estimates learned from real rescue data.

For an uncertain cue, calculate interval endpoints by monotonicity:

```text
B_i^lo = 0.40 M_i^lo + 0.25 T_i^lo + 0.20 H_i^lo + 0.10 V_i^lo + 0.05 N_i^lo
B_i^hi = 0.40 M_i^hi + 0.25 T_i^hi + 0.20 H_i^hi + 0.10 V_i^hi + 0.05 N_i^hi
U_i(t) in [B_i^lo + A_i(t), B_i^hi + A_i(t)]
```

**A lower endpoint is not a belief that unknown facts are false.** If intervals overlap, do not assert that the two reports have a definitive ranking. Show the uncertainty and request a coordinator's review; record any resolved information and recompute as a new versioned assessment. A high-impact hedged or conflicting statement is surfaced in the review queue even if its lower bound is small. Do not multiply a medical cue by the LLM's self-reported confidence: such a number does not establish the probability of injury or the correctness of extraction.

## 3. Waiting and the starvation claim

At the suggested `lambda=0.20`, waiting adds `0.20` points per 30 minutes. Since the static part of any **new** report is at most 1, an existing report with `B>=0` reaches at least 1 after 150 minutes and is then ranked no lower than a newly arrived report with the maximum static score; receipt-time tie-breaking favors the older report. After 150 minutes it outranks such a new arrival. Reassess every time the pending queue or evidence changes; do not reset `received_at` when text is edited or roads close.

This is a **limited anti-starvation property** against repeated *new* arrivals. It does not prove timely service if arrivals exceed capacity, if a case is unreachable, if earlier cases never finish, or if a dispatcher appropriately overrides the ordering. Add configurable review alerts at 30, 60, and 120 minutes rather than claiming an operational guarantee. If prolonged waiting would rank a report over a newly reported urgent sign, display both reasons and require a human decision. Choosing a smaller `lambda` lengthens the crossing time, e.g. `T_ref/lambda` minutes when the static-score gap is 1 (150 minutes for 30/0.20).

## 4. Workflow, examples and testable output

`PriorityAssessment` should record source snapshot, `policy_version`, cue states and evidence IDs, static interval, waiting contribution, total interval, review flags, and text explanations. Its existing `priority_weight` may remain `null` when there is no justified numeric weight for a downstream optimizer. Do not use this prototype `U` as a multiplier on travel time by default. The dispatch policy consumes reviewed ordering separately from a feasible route matrix, with deterministic receipt-time and SOS-ID tie-breaking. A simulation may emulate reviews only with a documented, fixed assumption applied equally to RescueAI and both baselines.

For the **small MVP dispatch example**, filter out SOS records without an accepted routing anchor and infeasible team–SOS pairs, while prominently flagging the excluded records. Sort routable, sufficiently reviewed reports by descending `U` (ties by receipt time, then ID); if priority intervals overlap materially, obtain a human queue-order decision. With a small batch and one assignment per team/SOS, choose a feasible matching that **lexicographically maximizes the served/not-served vector in that approved SOS order**; among matchings with identical served vectors, minimize the sum of feasible travel times. This keeps priority and travel as two explicit stages, without an undocumented `U × ETA` penalty. A dispatcher approves the proposal; handling durations and further assignments remain in the simulation/dispatch policy, not in this scoring rule.

Suggested **attention labels**, not clinical classes:

- `immediate_review`: an explicitly reported urgent sign, or reported trapped status together with an active hazard; always expose evidence and uncertainty.
- `elevated_review`: other explicit medical, trapped or hazard cue.
- `insufficient_information`: no supported cue settles the situation and material uncertainty could change the suggestion; request clarification. An unrouteable location always raises an additional location alert.
- `standard_review`: remaining well-reviewed reports; still pending until acted upon, with waiting alerts.

The cue score remains visible alongside these labels; staff can override any label with a logged reason. The label rules and coefficient versions must be documented together. A clinician-supplied triage category, if ever available, belongs to a **separate supervised override** and must not be added to `M` again. `access_observations` can change feasible teams and routes but never `B`.

### Worked calculation 1 — same facts as synthetic SOS `syn-v1-000001`

Assume the report explicitly says **10 people, some injury, rising water, structural cracks**, and has waited 30 minutes. It does **not** state urgent symptoms, trapped status or vulnerable people. Then `M in [0.5,1]`, `T in [0,1]`, `H=1`, `V in [0,1]`, `N=g(10)=1`, and `A=0.20`:

```text
B_lo = 0.40(0.5) + 0.25(0) + 0.20(1) + 0.10(0) + 0.05(1) = 0.45
B_hi = 0.40(1)   + 0.25(1) + 0.20(1) + 0.10(1) + 0.05(1) = 1.00
U in [0.65, 1.20] after 30 minutes
```

`0.65` is a lower **bound**, not “65% urgent”. The unresolved signs/trapped/vulnerability require review; the report is not assigned a confident negative for any of them. The environmental indicator counts once despite both water and structural claims.

### Worked calculation 2 — explicit negative facts and aging

Suppose a different report explicitly denies injury, urgent signs, trapped status, all hazard categories and vulnerable groups, and reports one person. After 120 minutes `M=T=H=V=N=0` and `A=0.20(120/30)=0.80`, hence `U=0.80`. At 150 minutes `U=1.00`, tying a just-arrived report with the maximal static score; the older received timestamp breaks that tie. This illustrates the rule's behavior, **not** an instruction to overrule an urgent human assessment.

### Pseudocode

```python
def assess(sos, now, config):
    facts = sos.reviewed_or_provisional_facts
    medical = range_of_max(range_of_urgent_sign(facts.urgent_signs),
                           scale(range_of_injury(facts.injury_reported), 0.5))
    trapped = range_of_explicit_bool(facts.trapped)
    hazard = range_of_any_positive_or_all_explicit_negative(
        facts.water_signals, facts.fire_signals, facts.structure_signals)
    vulnerable = range_of_explicit_group_presence(facts.vulnerable_groups)
    people = range_of_saturated_count(facts.people_count, cap=10)

    base = weighted_sum_intervals(config.weights,
                                  [medical, trapped, hazard, vulnerable, people])
    wait_minutes = max(0, (now - sos.received_at).total_seconds() / 60)
    aging = config.aging_per_30_minutes * wait_minutes / 30
    total = (base.low + aging, base.high + aging)
    flags = review_flags(facts, sos.location_resolution, wait_minutes)
    label = attention_label_from_reported_cues_and_flags(facts, flags)
    return immutable_assessment(total, base, aging, flags, label,
                                evidence_refs(facts), config.version)

# Separately: routing computes feasible[k, sos] and travel_time[k, sos]
# The dispatcher approves or rejects the resulting assignment proposal.
```

### Minimum tests

| Case | Expected behavior |
| --- | --- |
| All facts unknown | Static interval `[0,1]`, review required; never output a confident zero urgency. |
| Injury supported true, urgent signs unknown | `M in [0.5,1]`; no double counting and no invented symptom. |
| Urgent sign supported, injury unknown | `M=1` regardless of injury uncertainty. |
| Explicit injury false and urgent signs empty | `M=0`; the negative must have explicit evidence. |
| Water rising and structural cracks together | `H=1`, not `2`; avoid double counting. |
| Count 1, 10, 30 | `N=0,1,1`; the cap is applied. |
| Count conflicting between 2 and 5 | Count unresolved with review flag; do not silently choose either value. |
| Reporter-only GPS or unknown incident point | Score can be assessed; no route or ETA until a human-approved incident anchor exists. |
| Vehicle approach blocked / graph road closed | Urgency unchanged; routing feasibility/ETA changes and a new proposal needs approval. |
| Same SOS and facts, teams moved farther away | Same `U` interval; only routing results and proposals change. |
| Wait 0, 30, 150 minutes with base 0 | Aging `0,0.20,1.00`; no reset after a road update. |
| Same complete score and receipt time | Stable SOS-ID tie-break. |
| AI extraction low-confidence or hedged | Surface review and interval; never multiply score by raw model confidence. |

## 5. Sensitivity analysis and ablations

Pre-register parameter ranges **before** inspecting the frozen test results. On identical synthetic arrivals, teams, handling durations, graph events and simulated human-approval policy, vary:

- Static coefficients around the illustrative vector (e.g. each from `0.5x` to `1.5x` its baseline, then renormalize to sum to 1); disclose every tested vector.
- Aging `lambda in {0.10, 0.20, 0.30}` per 30 minutes; optional 30/60/90-minute review-alert schedules. The maximal-static-gap crossing times are 300/150/100 minutes respectively.
- People-count saturation at `5`, `10` or `20`; test a version without `V` to examine ethical sensitivity, and a version that uses only explicitly reviewed facts.

For every policy record how often the top-ranked SOS changes, Kendall rank correlation/top-k overlap, maximum and 90th-percentile wait, served coverage (including reports with urgent signs), mean response time, critical-cue response time and total travel distance. Report raw scenario outputs and uncertainty across scenario seeds, not only a single aggregate; distinguish counterfactual simulation from a claim of saved lives. Audit whether incomplete reports or those mentioning vulnerable groups systematically wait longer. In the extraction benchmark, measure false-certainty separately from the dispatch simulation.

Recommended **one-at-a-time ablations**: remove aging (`lambda=0`); remove `M`, `T`, `H`, `V` or `N` one by one without silently redistributing the deleted coefficient; replace interval review with a naive zero-fill **only as an explicitly unsafe diagnostic**; compare FCFS and nearest-feasible baselines under the same routing and approval assumptions. If you additionally renormalize remaining weights, report it as a separate experiment because it changes all other contributions.

## 6. Practical limits and scope

**MUST HAVE:** additive documented cues, interval uncertainty, uncapped aging with wait alerts, separate routing, human review/approval, deterministic tests and reproducible simulations. **NICE TO HAVE:** independent operational input from trained responders, calibrated extraction confidence (still not a probability of medical danger), and expert-reviewed coefficient elicitation. **POST-COMPETITION:** clinically appropriate triage integration, real-world validation and governance with emergency-response authorities.

The example coefficients reflect no clinical study. SOS reports may be wrong, stale, adversarial or incomplete. A larger group count or a flagged vulnerable category is an imperfect proxy and can affect equity; a single fixed rule may fail under resource scarcity or a new disaster. Policies and overrides need audit logs, evidence visibility, privacy controls and external review before any operational use. NIST's AI Risk Management Framework calls for documented evaluation, human oversight and assessment of safety, privacy and fairness in context: https://airc.nist.gov/airmf-resources/airmf/5-sec-core/ .
