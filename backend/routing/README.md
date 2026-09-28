# Dynamic road graph

`DynamicRoadGraph` is an in-memory, directed road network with deterministic
Dijkstra routing. It is domain logic; it does not call an AI model or an
external routing service. Add a separate reverse `Road` edge for two-way
travel. The graph does not infer a reverse connection.

## Road and cost contracts

Every `backend.routing.Road` requires an edge ID, distinct source and target
node IDs, `distance_meters`, `base_travel_time_seconds`, `risk_score`, a
`RoadStatus`, and a timezone-aware `last_updated` timestamp. Its status is
`OPEN`, `DEGRADED`, or `BLOCKED`. Distance and risk must be nonnegative;
base travel time must be positive. The caller defines the scale of the
nonnegative risk score and supplies a `RoutingCostConfig` explicitly. Its
`risk_penalty_seconds_per_unit` must be nonnegative and its
`degraded_time_multiplier` must be at least one.

For each traversable edge, the routing objective is:

```text
effective_travel_time_seconds = base_travel_time_seconds
    * (degraded_time_multiplier if status == DEGRADED else 1)
effective_cost_seconds = effective_travel_time_seconds
    + risk_penalty_seconds_per_unit * risk_score
```

`BLOCKED` edges are excluded from search. `distance_meters` is reported on a
route but does not enter this cost formula. `SafeRoute.travel_time_seconds`
includes any degraded-road slowdown and **excludes** the risk penalty;
`SafeRoute.effective_cost_seconds` is the optimization objective. The latter
is a preference score expressed in seconds, not a predicted arrival time.
Choosing a risk-score scale and penalty is a scenario/policy decision; the
module does not calibrate either from observations.

The older `backend.domain.models.RoadEdge` is a placeholder with different
fields. Use `backend.routing.Road` for graph storage and routing. The graph's
`find_route` and `apply_event` methods adapt to the existing
`backend.routing.contracts.RoadNetwork` dispatch protocol.

## Operations

Create `DynamicRoadGraph(cost_config)` before adding roads. `add_road` creates
its endpoint nodes; `add_node` can retain an isolated node. `update_road`
replaces all road attributes, including endpoints. `remove_road` removes the
edge and leaves its nodes. `block_road`, `unblock_road`, and `update_risk`
modify a named edge using the supplied timezone-aware update timestamp.
Updates older than the existing `last_updated` are rejected. `unblock_road`
sets status to `OPEN`; use `update_road` to set `DEGRADED`.

`shortest_safe_route(origin, destination)` returns a `SafeRoute` for the
current graph revision or `None` if either node is absent or no feasible path
exists. It returns a zero-length route when origin and destination are the
same existing node. `recompute_route(previous_route, current_node_id=None)`
reruns the search against current roads to the previous destination. It starts
at the previous origin unless the caller supplies its **current graph node**.
Handling a vehicle currently inside an edge belongs to the simulation layer.

`get_road(edge_id)` returns one current road. `get_state()` returns a sorted,
immutable snapshot of node IDs, roads, routing configuration, and revision.
Mutations increment the revision; `SafeRoute.graph_revision` identifies the
state used to compute it. Changing the cost configuration through
`set_cost_config` affects subsequent searches and increments the revision
when the configuration changes.

Dijkstra minimizes the sum of `effective_cost_seconds` across a path.
All edge costs are nonnegative. Neighbor iteration is sorted, and equal-cost
paths resolve by lexicographically smaller sequences of edge IDs, then node
IDs, independent of insertion order. A* is not implemented because this graph
does not store node coordinates or an admissible distance/time heuristic.

## Example

```python
from datetime import UTC, datetime, timedelta

from backend.routing import DynamicRoadGraph, Road, RoadStatus, RoutingCostConfig

started = datetime(2026, 1, 1, tzinfo=UTC)
graph = DynamicRoadGraph(
    RoutingCostConfig(
        risk_penalty_seconds_per_unit=20.0,
        degraded_time_multiplier=1.5,
    )
)
for edge_id, source, target, seconds in (
    ("ab", "a", "b", 10.0),
    ("bc", "b", "c", 10.0),
    ("ac", "a", "c", 30.0),
):
    graph.add_road(
        Road(
            edge_id=edge_id,
            source_node_id=source,
            target_node_id=target,
            distance_meters=100.0,
            base_travel_time_seconds=seconds,
            risk_score=0.0,
            status=RoadStatus.OPEN,
            last_updated=started,
        )
    )

original = graph.shortest_safe_route("a", "c")  # ab -> bc
graph.block_road("bc", updated_at=started + timedelta(minutes=1))
replanned = graph.recompute_route(original) if original is not None else None
# replanned uses ac; bc is blocked
```

The reproducible latency workload and measured results are described in
[evaluation/results/README.md](../../evaluation/results/README.md). Run it from
the repository root:

```sh
.venv/bin/python -m evaluation.road_graph_benchmark \
  --sides 5 10 20 30 --seed 412073 --warmups 10 --repeats 100 \
  --output evaluation/results/road_graph_latency.json
```
