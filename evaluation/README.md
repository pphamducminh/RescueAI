# Evaluation

The [synthetic SOS benchmark package](sos_benchmark/README.md) generates
text-only Vietnamese SOS records offline and validates/summarizes their design
distributions. It does not score an extractor. The separate dispatch comparison
below tests assignment policies on synthetic, typed requests and graph routes;
it does not run SOS extraction or priority inference.

## Dispatch strategy comparison

Run from the repository root:

```sh
.venv/bin/python -m evaluation.dispatch_comparison \
  --seed 412073 --scenarios 8 --horizon-seconds 3600 \
  --output-prefix evaluation/results/dispatch_comparison
```

The command writes [full raw JSON](results/dispatch_comparison.json),
[per-request CSV](results/dispatch_comparison.csv), and
[per-scenario CSV](results/dispatch_comparison.scenarios.csv). `--scenarios`
counts the whole workload: the first two scenarios, when requested, are
hand-constructed controls (including the approved 2×2 example); the rest are
generated from a local seeded random source. The `scenario_origin` field
distinguishes them. The JSON records complete scenario input snapshots,
per-request rows, and per-scenario metrics. Each scenario's immutable
route/eligibility snapshot is passed to FCFS, NearestTeam,
PriorityAwareGreedy, and RescueAIOptimizer; the seeded inputs and policy
weights are identical across strategies. There is no embedded winner or
performance conclusion.

This is a **single-wave counterfactual simulation**. It assumes immediate
simulated dispatcher approval at the decision time and first arrival after
the chosen route's travel time. It does not model movement along edges,
service duration, later dispatch waves, replanning, evacuation, actual human
approval, or successful rescue. Every scenario has a prespecified evaluation
horizon. A request is `reached_by_horizon` only when its predicted first
response time, including wait since server receipt, is at most that horizon.
Unserved requests have no fabricated arrival; their row has a null predicted
response and an explicit reason. A request assigned but arriving later than
the horizon is also unreached for horizon metrics.

Per-scenario metrics include reached count and coverage, reached-only mean
response, reached-only mean response for synthetic critical cues, fixed
evaluation-weighted reached-only mean response, route distance, objective
`P/Q/K/C`, and measured solver wall time in nanoseconds. Reached-only means
are null when their denominator is zero. The
`evaluation_weighted_capped_all_request_seconds` reporting convention caps
arrivals at the prespecified horizon and assigns that cap to requests not
reached. It is not a measured arrival time. Dispatch weights are declared
synthetic inputs for the assignment objective; separate evaluation weights
are fixed before strategy runs. Critical cues and those weights have no
clinical validation. Compare coverage alongside reached-only means, since
serving fewer requests can make such a mean appear lower.

The random scenarios use small directed team-to-request pair graphs with
synthetic road travel times, risk, and statuses. They are not geographic road
networks. The graph selects a path by configured travel-plus-risk effective
cost before dispatch assigns teams using those paths' travel ETAs; the
comparison does not jointly optimize paths and assignments. The JSON records
the graph-derived routes and cost configuration.
Measured solver latency varies by machine and run even when seeded inputs and
assignments replay identically. Control scenarios should be reported
separately from generated scenarios when estimating a workload distribution.
See the [dispatch guide](../backend/dispatch/README.md) for policy definitions
and the [approved formulation](../docs/RescueAI%20Assignment%20Model%20v1.md)
for objective assumptions.

## Reproducible dispatch benchmark

Run the generated-only comparison from the repository root:

```sh
.venv/bin/python -m evaluation.run \
  --scenarios 100 --seeds 1 2 3 4 5 \
  --strategies fcfs nearest greedy rescueai \
  --horizon-seconds 7200 \
  --output-dir evaluation/results/benchmark
```

`--scenarios` is the number of **generated scenarios per seed**. The example
requests 500 scenario snapshots and one run of each of the four strategies on
every snapshot. It does not insert the hand-constructed controls used by the
older `evaluation.dispatch_comparison` command. Each strategy receives the
same frozen routes, availability, capabilities, and evaluation weights for a
scenario. The strategy aliases in the example resolve as follows:

| CLI name | Registered strategy |
| --- | --- |
| `fcfs` | `fcfs` |
| `nearest` | `nearest_team` |
| `greedy` | `priority_aware_greedy` |
| `rescueai` | `rescueai_optimizer` |

The registered names are accepted directly as well. The runner saves these
files in `--output-dir`:

| File | Contents |
| --- | --- |
| `manifest.json` | CLI configuration, seeds, strategy names, generator and route-cost settings, environment, code hash, row counts, and measurement scope. |
| `scenarios.jsonl` | Full typed state snapshot per `(seed, scenario, variant)`, with a hash; the same snapshot is used by every selected strategy. |
| `runs.csv` | One raw row per strategy and scenario, including status, solver wall time in nanoseconds, coverage, response metrics, route distance, and `P/Q/K/C` objective values. |
| `requests.csv` | One raw row per request and strategy, including assignment, route, predicted response, reached flag, explicit unserved reason, and capped reporting value. |
| `summary.json` | Validated descriptive statistics grouped by variant, scenario origin, and strategy, plus failure records. |

Only `strategy.solve(state)` is timed; graph construction, route calculation,
metric calculation, and file output are outside the timer. The measured
`solve_latency_ns` is wall time and can vary with machine load even when the
seeded scenarios and assignments replay identically. It is not added to the
simulated response time. The manifest records the seed and a hash of the
relevant source files; it does not represent a frozen release or field trace.

`evaluation.run` checks for missing or non-finite values before output. Its
aggregator rereads the saved CSV and JSONL, checks scenario hashes, complete
paired strategy coverage, required columns, finite metrics, and agreement
between request rows and run metrics. Recheck an existing run with:

```sh
.venv/bin/python -m evaluation.aggregate_metrics \
  --input-dir evaluation/results/benchmark
```

If a strategy raises an error, the raw run stays in the output with
`run_status=solver_error`, the error type/message, no assignments, and a
declared horizon cap for each request. Such a run is counted in the summary's
failure list and has zero horizon coverage. A missing denominator for a
reached-only or critical-only metric is recorded as null, not zero. The
aggregator treats each **scenario run** as one observation, keeps control and
generated origins separate, and reports simple descriptive means of available
scenario metrics with their counts and null counts. It does not pool SOS rows
as independent replicates, average `P/Q/K/C` across unlike scenarios, or
calculate significance or confidence intervals.

The weighted all-request capped response uses evaluation weights fixed before
any strategy runs. For each request, an arrival after the declared horizon or
no arrival is reported at the horizon cap; the cap is a reporting convention,
not an observed arrival. Read reached-only response alongside coverage and
the failure count. Route distance in this one-wave runner is planned distance;
it is not a measured vehicle trajectory.

### Uniform dispatch-weight ablation

```sh
.venv/bin/python -m evaluation.run_ablation \
  --scenarios 100 --seeds 1 2 3 4 5 --strategies rescueai \
  --horizon-seconds 7200 \
  --output-dir evaluation/results/weight_ablation
```

The ablation uses the same generated source scenario for `baseline` and
`uniform_weights`. The latter changes each already approved dispatch weight
to one. Requests whose dispatch weight is pending review remain pending, and
the fixed evaluation weights, graph, teams, and routes are unchanged. Source
and variant hashes allow pairs to be checked. This command writes the same
five raw and summary files as `evaluation.run`. It does not implement the
proposed no-aging, periodic-replanning, or input-quality ablations.

### Routing computation trials

```sh
.venv/bin/python -m evaluation.benchmark_routing \
  --sides 5 10 20 30 --seed 412073 --warmups 3 --repeats 30 \
  --output-dir evaluation/results/routing_benchmark
```

`routing_trials.csv` holds every measured Dijkstra route, road block, and
replan trial; `routing_summary.json` records the workload, seed, routing cost
configuration, timing scope, environment, and descriptive latency by grid
size. Grid generation and warmups are excluded from the measured operations.
The selected blocked edge belongs to the baseline route; each replan must
avoid it. These are local computation times, not dispatch response or travel
times. The separate older `evaluation.road_graph_benchmark` command below
preserves its earlier summary-only result.

These scripts implement a one-wave synthetic comparison. They do not execute
the [proposed Evaluation Protocol v1](../docs/RescueAI%20Evaluation%20Protocol%20v1.md):
there is no event-driven playback, team service duration, road-update replay,
factorial design, paired uncertainty analysis, or held-out outcome claim.
These scripts produce no plots.

## Road graph latency

The [road graph latency benchmark](results/README.md) measures Dijkstra route
computation, one road-block update, and route recomputation on deterministic
directed grids of 25, 100, 400, and 900 nodes. Its
[JSON result](results/road_graph_latency.json) contains actual measured min,
median, mean, p95, and max times with the seed, workload, machine, and Python
version. These are local computation times, not simulated response times or
end-to-end dispatch measurements.

Extraction precision, recall, and F1 still require a reviewed labeled dataset
and a documented scoring method. The current synthetic SOS development
sample has not been human reviewed.
