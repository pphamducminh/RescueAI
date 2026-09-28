# One-wave dispatch strategies

`backend.dispatch` proposes one team per SOS and at most one SOS per available
team in a single decision wave. It implements the policy in
[RescueAI Assignment Model v1](../../docs/RescueAI%20Assignment%20Model%20v1.md).
Every `DispatchPlan` has status `awaiting_human_approval`: solving a snapshot
does not authorize or execute a dispatch.

## Shared input and output

Create typed `DispatchRequest` and `DispatchTeam` values, then call
`build_dispatch_state(scenario_id, decision_time, requests, teams, graph,
weight_policy_version)`. The builder sorts IDs and computes every eligible
team-to-incident route from one `DynamicRoadGraph` revision. An accepted
incident routing anchor, an available team, sufficient declared capabilities,
and a passable route are required for a `FeasiblePair`. All strategies receive
that same `DispatchState`; blocked roads yield no pair. Rebuild the state after
a graph update to plan against its new revision.

`DispatchRequest.dispatch_weight` is an explicitly supplied positive integer
or `None` when weight review is pending. `evaluation_weight` is a separate,
fixed positive integer for comparisons. This package does not derive either
weight from SOS text or establish the review decision. A request without an
accepted routing anchor or dispatch weight stays visible in
`DispatchPlan.unserved_requests` with a review reason. An approved-weight
request that cannot be assigned also stays visible. Pending means unassigned
in this wave; it does not mean resolved or refused.

Each plan contains assignments, the chosen `SafeRoute` on the snapshot graph,
an integer planning ETA, predicted arrival and response times, objective
values, unserved reasons, and explanation metadata including the rule, solver
status, and solver backend. Predicted response time includes waiting since
server receipt.
No arrival time is invented for an unserved request. The builder rounds each
route's travel time **up to a whole second** for integer optimization; predicted
arrival and response use the route's actual travel time. The graph first
chooses a route that minimizes its configured effective cost (travel time plus
risk penalty). Dispatch then optimizes assignments using the whole-second
**travel ETA of those fixed feasible routes**. It does not jointly optimize
routes and assignments, and the route's risk penalty is not added to predicted
arrival time.

The common interface is `DispatchStrategy.solve(state) -> DispatchPlan`.
`list_strategies()` exposes these stable registry names, and `get_strategy(name)`
constructs a fresh, stateless strategy:

| Name | One-wave rule |
| --- | --- |
| `fcfs` | Oldest server receipt first, then fastest free eligible team. |
| `nearest_team` | Repeatedly take the globally lowest feasible integer ETA. |
| `priority_aware_greedy` | Highest approved weight first, then oldest receipt and fastest free team. |
| `rescueai_optimizer` | Exact joint assignment minimizing weighted pending loss, then weighted ETA. |

Every rule breaks its selection ties with fixed IDs; the optimizer sorts
request and team IDs and scans matching columns in a fixed order. This gives
repeatable assignments for identical inputs. It does not establish a separate
lexicographic optimum among all equal-cost matchings.

## Objective and solver

Only requests with an approved `dispatch_weight` enter the optimization
objective. For those requests, let `w_i` be that weight, `z_i=1` when still
pending, and `t_ij` be the rounded integer ETA of a feasible team–request
pair. Each request is assigned once or pending; each team is assigned at most
once. The objective is lexicographic:

```text
P = sum_i w_i z_i                  weighted pending coverage loss
Q = sum_(i,j) w_i t_ij x_ij       weighted first-arrival travel seconds
minimize P, then minimize Q among plans with minimum P
```

For a snapshot, `T_i^max` is request `i`'s largest feasible integer ETA, or
zero if none exists. The code computes `K = 1 + sum_i w_i T_i^max` and solves
`C = K P + Q`. Since `Q < K`, a one-unit decrease in `P` always takes
precedence over any possible change in `Q`. `DispatchPlan.objective_value`
reports all four values for every strategy, so baseline plans can be compared
on the same scale. `w_i` is a declared engineering ranking weight, not an
LLM confidence or a clinical claim. A weight-pending-review request is
excluded from `P`, `Q`, and `K`, while remaining in the plan's unserved list.

The approved design sketches OR-Tools integer min-cost flow and a checked
64-bit cost bound. This implementation uses an exact rectangular Hungarian
matching with one pending column per request and Python's arbitrary-precision
integers. It solves the same one-wave `P` then `Q` assignment objective without
requiring OR-Tools, but it is a different solver backend. No optimization
result is claimed for future waves, multi-stop routing, evacuation capacity,
or human outcomes.

[RescueAI Priority Model v1](../../docs/RescueAI%20Priority%20Model%20v1.md)
earlier suggested maximizing a served/not-served vector in approved queue
order before minimizing travel. That objective can disagree with weighted
pending loss. This implementation follows the later, dedicated Assignment
Model v1 formulation; the difference is a policy decision to revisit before
operational use.

## Offline comparison

From the repository root:

```sh
.venv/bin/python -m evaluation.dispatch_comparison \
  --seed 412073 --scenarios 8 --horizon-seconds 3600 \
  --output-prefix evaluation/results/dispatch_comparison
```

The comparison writes raw per-request and per-scenario CSV files plus a JSON
report with the same rows and scenario input snapshots. It uses two
hand-constructed controls when the count permits, then seeded synthetic
scenarios. All four policies see each scenario's exact same state and route
matrix. See [evaluation/README.md](../../evaluation/README.md) for metric
definitions and simulation assumptions. The dispatcher approval assumption
in this comparison is simulated; there is no operational approval workflow.
