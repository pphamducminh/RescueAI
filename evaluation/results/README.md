# Road graph latency results

`road_graph_latency.json` contains actual measurements from
`evaluation.road_graph_benchmark`. Reproduce the workload from the repository
root with:

```sh
.venv/bin/python -m evaluation.road_graph_benchmark \
  --sides 5 10 20 30 --seed 412073 --warmups 10 --repeats 100 \
  --output evaluation/results/road_graph_latency.json
```

Each side length gives a directed square grid with reverse edges (25, 100,
400, and 900 nodes). Edge risk values come from the seeded Python random
generator; road insertion order, endpoints, base times, and timestamps are
fixed. Each trial measures a Dijkstra route, then the time to block one edge
on that route, then the time to recompute from the original origin. The blocked
edge must be absent from the recomputed path. The road is unblocked before the
next trial. Graph construction, warmups, and the unblock operation are not
included in the route or replanning timings.

Latency statistics use nanoseconds from `time.perf_counter_ns()`. The report
records the machine, Python version, parameters, and UTC measurement time.
Measurements are workload and machine specific; they are not service latency
guarantees. This benchmark uses only local deterministic graph operations and
does not call an AI model or network service.
