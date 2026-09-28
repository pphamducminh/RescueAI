# Synthetic dispatch comparison v1

The files `dispatch_comparison.json`, `dispatch_comparison.csv`, and
`dispatch_comparison.scenarios.csv` are raw
results produced by `evaluation.dispatch_comparison`, not claims about real
rescue outcomes. Reproduce them from the repository root with:

```sh
.venv/bin/python -m evaluation.dispatch_comparison \
  --seed 412073 --scenarios 8 --horizon-seconds 3600 \
  --output-prefix evaluation/results/dispatch_comparison
```

The default experiment contains two explicitly marked
`hand_constructed_control` scenarios and six `seeded_generated` scenarios.
The first control encodes the approved assignment-model 2×2 example; the
second covers blocked roads, an unaccepted incident anchor, an unapproved
dispatch weight, and insufficient teams. The generated cases use a local
`random.Random(seed)` instance and small directed team-to-incident road graphs.
Roads may be open, degraded, or blocked. Each scenario's frozen
`DispatchState`, including routes, weights, graph revision, and eligibility,
is stored in the JSON report and passed unchanged to every registered
strategy. Strategy inputs, decisions, and CSV rows are reproducible for a
given seed and code version. `solve_latency_ns` is directly measured wall
time and will vary across runs and machines.

## Simulation and metrics

Each scenario has one decision instant and one first-response assignment
wave. The experiment **simulates immediate dispatcher approval** of every
proposal at that instant, then treats the route ETA as the first-arrival
time. It does not simulate road traversal, handling time, later arrivals,
multi-team work, destinations, or replanning. Production proposals still
require human approval; this is only an offline comparison assumption.

The request CSV has one row per `(scenario, strategy, request)` and records the
request's fixed evaluation weight, critical cue, assignment or pending
reasons, route, planned ETA, predicted first-response time, and planning
objective `P`, `Q`, `K`, `C`. JSON contains the same `request_metrics`, plus
`scenario_inputs` and one `scenario_metrics` row per strategy and scenario.
The scenario CSV repeats those per-strategy scenario metrics in tabular form,
including measured solve latency.
No cross-scenario winner or pooled average is computed; the diagnostic
controls are kept distinct from seeded scenarios.

Response time is `predicted_arrival_at - received_at`, including waiting
before the decision instant. A request counts as reached only if that time
is at most the prespecified `horizon_seconds` (3,600 by default). The
ordinary and critical-cue means are defined over reached requests only and
are JSON `null` if none were reached. The served-only evaluation-weighted
mean uses each request's fixed `evaluation_weight`, independent of strategy
decisions. The capped all-request metric uses the observed synthetic
response time capped at the horizon for reached requests and the full
horizon for every unreached request; it is a reporting convention, not a
fabricated arrival. Reach counts accompany every average. Route distance
is reported both for all planned assignments and for requests reached by
the horizon. The `P/Q/K/C` values are the common planning objective, using
approved dispatch weights and whole-second ceiling ETAs; they are distinct
from the fixed-weight reporting metrics.

Route generation uses a risk penalty of 20 seconds per risk unit and a 1.5
degraded-road travel-time multiplier. Blocked roads are infeasible. The
synthetic generator gives each request independent, explicitly known
`dispatch_weight`, `evaluation_weight`, and `critical_cue` values. These
are experiment labels, not medical judgments or model predictions.

## Limits

This comparison is a single-wave assignment replay, not an event-driven
fleet simulation or a causal study. The generated graph topology has direct
team-to-request edges and intentionally small instances; it does not cover
real road geometry or travel uncertainty. The hand-constructed examples
verify expected policy behavior and should not be pooled with generated
cases for a performance claim. Planning `solver_status` identifies greedy
or exact optimizer output, whereas `plan_status` always denotes a proposal
awaiting human approval outside this simulated evaluation.
