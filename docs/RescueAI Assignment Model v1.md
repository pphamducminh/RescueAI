# RescueAI — rescue-team assignment model v1

**Status:** competition MVP design, 2026-09-28. This is decision support in a synthetic or otherwise explicitly authorized setting. A human dispatcher approves every operational proposal. Priority weights, thresholds and costs below are illustrative engineering settings, not clinically validated judgments or estimates of lives saved.

## 1. Decision and data boundary

At a decision time `now`, take all pending SOS requests `I` and currently available teams `J`. The MVP makes a **single-wave, one-team-to-one-SOS first-response assignment**. Each available team may receive at most one assignment in this wave. A request can be assigned once or remain visibly pending for review/replanning. Run the optimizer again when reports arrive, teams finish work, graph edges change, or a dispatcher corrects the facts. An approved assignment is not silently revoked by a new proposal.

The pipeline is:

`SOSInput -> ExtractedSOS -> ValidatedSOS -> PriorityAssessment -> dispatcher-approved dispatch weight`;
independently, `road graph + incident routing anchor + team position -> feasible route + ETA`;
then `priority weights + routes + eligibility -> assignment proposal -> human approval`.

The extraction model **never supplies a final medical diagnosis, confirmed road state, verified location, or dispatch authorization**. The incident routing anchor must be accepted by a dispatcher; a reporter's GPS location cannot silently stand in for the incident. A reported injury is not by itself a verified medical emergency. The score in *RescueAI Priority Model v1* includes an uncertainty interval and elapsed waiting time; its unreviewed value is not automatically multiplied by ETA. For experiments requiring priority-weighted response time, introduce a separately versioned and reviewable `dispatch_weight` policy. If uncertainty crosses dispatch tiers, record `weight_pending_review` and surface it to the dispatcher rather than filling a missing fact with zero.

| Input | Symbol | Meaning |
| --- | --- | --- |
| Feasible team–request pairs | `E ⊆ I × J` | Available team, sufficiently confirmed capability, accepted routing anchor, and passable route. Unknown capability/route is an explicit review state, not eligibility. |
| Response travel time | `t_ij` | Shortest acceptable **team-to-incident** ETA, seconds, from the same version of the dynamic graph for every policy. Record the chosen path and route version. |
| Route distance | `d_ij` | Distance for secondary reporting; no default distance penalty in the urgency policy. |
| Approved dispatch weight | `w_i ≥ 1`, integer | A disclosed ranking/metric weight from the reviewed priority policy, not an LLM confidence or a probability. It can be recomputed over time by the priority module's published aging rule. |
| Fixed evaluation weight | `v_i ≥ 1` | Chosen once per synthetic scenario from independently known scenario labels, or once from the same human-reviewed record for all policies. Never recomputed from a policy's own outcomes. |
| Waiting so far | `a_i` | `max(0, now - server_received_at_i)`; included in observed response time. Do not reset on report edits. |
| Critical cue for reporting | `c_i ∈ {0,1}` | Human-reviewed synthetic scenario or documented operational category. Keep its provenance; do not infer a clinical category from an LLM. |

**Explicit example weight policy:** For a *reviewed, single-valued* priority assessment from the previous priority model, `w_i(now) = 1 + floor(4 U_i(now))`; use exact decimal/rational arithmetic at tier boundaries. At static `U=0` this is weight 1, at `U=1` weight 5, and the previously documented aging term inside `U(now)` can subsequently raise it; **do not add waiting a second time**. An unresolved interval `[U_lo,U_hi]` has an approved weight only if `1+floor(4 U_lo) = 1+floor(4 U_hi)` under the policy's precise boundary convention; otherwise obtain and record a dispatcher decision about the dispatch weight *without pretending the unknown underlying facts became known*. Weights `5` and `1` in the worked example below are declared synthetic inputs, not clinical classes. This optional, **newly adopted** dispatch-weight policy deliberately changes the prior document's caution against multiplying unreviewed `U` by ETA; log its version and compare alternative mappings in sensitivity analysis.

**Capacity semantics:** Team dispatch capacity in the MVP is **one simultaneous incident**, represented by `sum_i x_ij ≤ 1`. Vehicle seats, stretcher count and ability to evacuate a reported group are different properties. Where a task explicitly requires single-trip evacuation, verified equipment, transport and seats can remove an ineligible `(i,j)` edge. If a group requires multiple vehicles/trips, raise `needs_multi_team_plan`; do not claim that one assigned response team can evacuate everyone. Hospitals/shelters and destination availability are separately checked downstream; the first-arrival ETA ends at the incident, not the destination.

When a hospital or shelter is optional in the demo, propose a destination **after** first-response assignment using only verified facility type, capacity/availability, destination route and any human-confirmed transport requirement. Display the incident-to-destination ETA separately and keep the destination pending if no appropriate facility has been verified. Shared facility capacity, handoff times and pickup-before-drop-off sequencing belong to the later routing extension.

## 2. Comparable policies

All policies use the **same** SOS arrivals, teams, eligibility `E`, road-graph snapshot, route ETA matrix, review/approval assumption and replan triggers. A request without a feasible team remains pending and visible.

| Policy | Choice/objective in one wave | Constraints and computational cost after building `E` | Strength | Limitation; implementation effort |
| --- | --- | --- | --- | --- |
| **A. FCFS** | Sort by server receipt time; for each, choose the fastest currently free eligible team. | Unique team/request, eligibility. `O(N log N + NM)` with a precomputed matrix. | Transparent age baseline. | Ignores other requests' travel and priority; very low effort. |
| **B. Nearest-team greedy** | Repeatedly choose the globally smallest feasible `(t_ij, request_id, team_id)`. | Unique team/request, eligibility. Sort at most `NM` edges: `O(NM log(NM))`. | Simple geographic baseline. | A short local trip can strand another request with a long remaining route; low effort. |
| **C. Priority-aware greedy** | Sort SOS by approved `w_i` (tie: older first), give each the fastest free eligible team. | Unique team/request, eligibility. `O(N log N + NM)`. | Easy to explain and honors queue ranking. | Can use a versatile team early even when another team would serve the urgent case almost as fast; low effort. |
| **D. Bipartite min-cost assignment/flow** | First minimize weighted pending coverage loss; **then** minimize priority-weighted first-arrival travel time. | Same constraints plus optional deferred SOS. A flow network has `O(N+M)` nodes, `O(NM)` arcs. Exact polynomial-time static matching; dense assignment can be solved in `O((N+M)^3)` with a cubic matching method, whereas actual min-cost-flow runtime depends on implementation. | Jointly allocates the fleet and exposes why a case is pending; medium effort. | One-wave optimality, not a guarantee of good future multi-stop scheduling. |
| **E. CP-SAT / vehicle routing** | CP-SAT: general integer objective; VRP: sequence stops, time windows, transport/load, destination. | More constraints such as multiple teams per SOS or pickup/drop-off precedence. General scheduling/routing is NP-hard, with exponential worst-case search; measure with a time limit. | Expressive for realistic later stages. | More modeling, testing and explanation; high effort for the MVP. |

OR-Tools documents `SimpleMinCostFlow` with integral arc capacities, costs and node supplies; it also documents CP-SAT's integer-only formulation and a VRP solver with capacity and time-window extensions. These are *solver capabilities*, not evidence of emergency-response validation. References: [min-cost flow](https://developers.google.com/optimization/flow/mincostflow), [CP-SAT](https://developers.google.com/optimization/cp/cp_solver), [vehicle routing](https://developers.google.com/optimization/routing/vrp).

## 3. Exact MVP formulation

Variables: `x_ij ∈ {0,1}` for `(i,j) ∈ E`, and `z_i ∈ {0,1}` for a request **still pending this wave**. `z_i` never means cancelled, refused or resolved.

```text
for every SOS i:     sum_{j:(i,j) in E} x_ij + z_i = 1
for every team j:    sum_{i:(i,j) in E} x_ij <= 1
outside E:           x_ij does not exist
```

**Lexicographic objective:**

```text
Stage 1: minimize  P = sum_i w_i z_i                      [weighted unmet coverage]
Stage 2: minimize  Q = sum_(i,j)∈E w_i t_ij x_ij          [weighted response travel]
         subject to P = P* from Stage 1.
```

Prioritizing coverage is an explicit policy choice when there are too few teams. A weight 5 request can compete with five weight 1 requests; that choice is **visible and configurable**, and a dispatcher can override it with an audit reason. `w_i` is an engineering weight, not a claim about survival, severity, or the relative value of people. When everyone can be served, `P*=0` and Stage 2 exactly optimizes priority-weighted first-response travel time for the current wave.

The realized response time includes the already-elapsed `a_i`, but `a_i` is fixed for each SOS at a given decision instant. For **all-request** planning with a common hypothetical extra delay `H` for each still-pending request, `sum_i w_i [a_i + sum_j t_ij x_ij + H z_i] = sum_i w_i a_i + Q + H P`. Once Stage 1 fixes `P=P*`, minimizing this expression is exactly minimizing `Q`, whatever common `H` is. This is a transparent planning surrogate, **not** a claim that a deferred SOS actually received a response. A served-only response-time average can move in a misleading direction when some requests are deferred; therefore report unresolved coverage and an explicitly capped all-request metric as well.

To implement both stages in **one** min-cost-flow solve with **integer** seconds, define `T_i^max = max_{j:(i,j)∈E} t_ij` (zero if no feasible team), and

```text
K = 1 + sum_i w_i T_i^max      [integer seconds; computed for this snapshot]
minimize C = K sum_i w_i z_i + sum_(i,j)∈E w_i t_ij x_ij.
```

Because any possible `Q` is at most `K-1`, a one-unit improvement in integer `P` always outweighs any change to `Q`; hence the single solve exactly implements the two-stage objective, assuming integral positive weights and times, no integer overflow, and the stated single-wave constraints. This `K` is a **derived bound**, not an arbitrarily chosen harm or waiting-time penalty. Use a checked 64-bit cost bound; fall back to two explicit solves if it could overflow. Fixed `request_id` and `team_id` ordering supports repeatable runs; add a truly subordinate, documented third-stage tie break if unique tie outcomes are required.

**Flow construction:** supply `+N` at source, demand `-N` at sink. Arcs `source→request_i` capacity 1 cost 0; `request_i→team_j` capacity 1 cost `w_i t_ij` for feasible pairs; `request_i→PENDING` capacity 1 cost `K w_i`; `team_j→sink` capacity 1 cost 0; `PENDING→sink` capacity N cost 0. This network automatically satisfies uniqueness and always has a pending path for every SOS. Unknown location and all-blocked roads create **no** request→team arcs, rather than a fabricated large ETA.

Distance is a secondary statistic and optional tie breaker. Do **not** add meters to seconds without stating a conversion coefficient and sensitivity analysis. The objective never alters the urgency score because of distance or lack of access.

### Worked synthetic example

Two available, capable teams `A,B`; two requests `C` (approved weight 5, older), `O` (approved weight 1). All four pairs are feasible.

| First-arrival ETA (minutes) | Critical-cue `C`, `w=5` | Other `O`, `w=1` |
| --- | ---: | ---: |
| Team A | 3 | 4 |
| Team B | 5 | 40 |

FCFS with `C` older, globally nearest-team greedy, and priority greedy all select `A→C` and `B→O`: `Q = 5×3 + 1×40 = 55` weighted minutes; unweighted travel time is 43 minutes. The min-cost assignment selects `B→C` and `A→O`: `Q = 5×5 + 1×4 = 29` weighted minutes; total arrival travel time is 9 minutes. Coverage is identical, and `P=0` in both solutions. This proves only that the stated synthetic instance distinguishes the algorithms.

## 4. Implementation sketch

```python
def propose(snapshot, priority_policy, graph, now):
    requests = sorted(snapshot.pending_sos, key=lambda r: r.id)
    teams = sorted(snapshot.available_teams, key=lambda t: t.id)
    # Keep unresolved SOS visible, even if it has no eligible route.
    E, eta_seconds, paths, distances = build_eligible_routes(
        requests, teams, graph, require_accepted_incident_anchor=True
    )
    weight = priority_policy.reviewed_dispatch_weights(requests, now)
    # A missing dispatch weight requires human priority review; no false default.
    review_only = {i for i in requests if weight[i] is None}
    candidate = [i for i in requests if i not in review_only]
    E = {(i, j) for (i, j) in E if i in candidate}

    t_max = {i: max((eta_seconds[i, j] for (a, j) in E if a == i),
                    default=0) for i in candidate}
    K = 1 + sum(weight[i] * t_max[i] for i in candidate)
    check_int64_cost_bounds(K, weight, eta_seconds, candidate)

    flow = SimpleMinCostFlow()
    source, pending, sink, req_node, team_node = allocate_nodes(candidate, teams)
    for i in candidate:
        flow.add_arc_with_capacity_and_unit_cost(source, req_node[i], 1, 0)
        flow.add_arc_with_capacity_and_unit_cost(req_node[i], pending, 1,
                                                 K * weight[i])
    for i, j in sorted(E):
        flow.add_arc_with_capacity_and_unit_cost(req_node[i], team_node[j], 1,
                                                 weight[i] * eta_seconds[i, j])
    for j in teams:
        flow.add_arc_with_capacity_and_unit_cost(team_node[j], sink, 1, 0)
    flow.add_arc_with_capacity_and_unit_cost(pending, sink, len(candidate), 0)
    flow.set_node_supply(source, len(candidate))
    flow.set_node_supply(sink, -len(candidate))
    status = flow.solve()
    if status != flow.OPTIMAL:
        return alert_dispatcher_with_no_automatic_assignment(status)

    assigned, still_pending = decode_unit_flows(flow)
    return proposal(assigned, still_pending, review_only, paths, distances,
                    snapshot.graph_version, priority_policy.version,
                    approval_state="awaiting_human_approval")
```

`allocate_nodes` assigns distinct integers; unspecified supplies default to zero (or set zero explicitly). Use an actual `ortools.graph.python.min_cost_flow.SimpleMinCostFlow` import and inspect the installed version's API. In production code, do not replace solver errors with a greedy dispatch silently. Recompute proposals on road changes, but let the human dispatcher review the affected approved route; an already-traversed edge follows the simulator's stated edge-closure rule.

## 5. Metrics, experiment and uncertainty

For request `i`, measure the realized first-response time `R_i = first_team_arrival_i - server_received_at_i` in the **simulation**, including the wait before dispatch. The optimizer may use the changing `w_i(now)` to counter waiting; for comparisons, **freeze** a separate evaluation weight `v_i` *before* running any dispatch policy. Otherwise a policy that makes someone wait longer could change its own metric weight. Compute `sum_i v_i R_i / sum_i v_i` over **served** requests only and always report its served count. If zero are served, show `N/A`. This served-only average is vulnerable to selection bias: pair it with the number/proportion unresolved at horizon, and optionally report a **capped all-request metric** `sum_i v_i min(R_i,H_eval)/sum_i v_i`, assigning `H_eval` to requests not reached by that prespecified horizon. The cap is a reporting convention, never a measured arrival time. Fix `v_i` and `H_eval` for all compared algorithms.

Report also ordinary mean response time among served, `sum_i R_i` among served, traveled road distance, served and unresolved counts, reviewed critical-cue coverage, critical-cue mean/90th-percentile wait when count permits, fraction of critical-cue cases not reached by `H_eval`, solver wall time and number of replans. Define the critical cue from scenario labels/human review, not from an LLM's unsupported diagnosis. Separate confirmed unreachable, route pending review, no eligible team, and capacity saturated in the dashboard; all count as not reached when appropriate.

Replay FCFS, nearest greedy, priority greedy and matching on identical seeded **synthetic** events and approved priority weights. Freeze inputs before comparing policies. Record `graph_version`, `priority_policy.version`, eligibility, every dispatch approval assumption, solver status, seeds and raw per-SOS arrival times. Assess sensitivity to priority-weight mappings, aging policy and graph changes; include an ablation with all `w_i=1`, one without dynamic replanning, and one that removes the joint matching while holding all other components fixed. Synthetic results demonstrate algorithm behavior under assumptions, not real-world benefit.

**Uncertainty:** A location without an accepted incident anchor is not routed. An uncertain closure/capability must be verified or flagged; it is not a passable edge by default. If a priority interval crosses dispatch-weight thresholds, request a human priority decision. If an interval maps to one and the same weight under a published policy, it may be used without inventing unknown facts; log that policy decision. Pending requests are revisited at every event; no optimization claim guarantees service when routes are blocked or demand exceeds capacity. Keep urgency independent of the road graph.

## 6. Acceptance tests and future extension

| Synthetic test | Expected result |
| --- | --- |
| Worked 2×2 example above | Assignment cost 29 weighted minutes, both served; every greedy policy chooses cost 55 given its stated order. |
| Three SOS, one team; known weights | At most one assigned, two explicitly pending; higher weighted coverage wins first. |
| One SOS unreachable for every team | No ETA or assignment is fabricated; request remains pending with route reason. |
| Team lacks required verified capability | Pair absent from `E`; no solver workaround. |
| Reporter GPS only | Incident anchor unresolved; no team route until reviewed. |
| SOS with unknown injury/trapped and overlapping weight tiers | `weight_pending_review`; no confident negative or numeric default. |
| Road closes after proposal | New graph version yields new proposal; earlier approved action changed only after human review. |
| Waiting increases and reviewed priority policy ages | Recomputed weight can change the next assignment; receipt time never resets. |
| More people than team seats on an evacuation task | Flag for multi-team/transport planning; do not mark evacuation completed from one first-arrival assignment. |
| Identical inputs/solver version | Replay same eligibility, objective and metric; documented tie procedure when assignments have equal costs. |

**One MVP algorithm:** lexicographic weighted-coverage / weighted-ETA **bipartite min-cost-flow assignment** with a pending arc and human approval.

**One later extension:** a **pickup-and-delivery vehicle-routing model with time windows and verified hospitals/shelters**, capable of modeling sequential jobs, patient/vehicle capacity, destination compatibility and transfer times. OR-Tools routing supports capacity, time windows and optional dropped visits, but this extension needs separately reviewed service times, destination availability and simulator tests. References: [VRP](https://developers.google.com/optimization/routing/vrp), [time windows](https://developers.google.com/optimization/routing/vrptw), [capacity](https://developers.google.com/optimization/routing/cvrp), [dropping visits](https://developers.google.com/optimization/routing/penalties).
