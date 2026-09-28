"""Mutable directed road graph with deterministic Dijkstra routing."""

from __future__ import annotations

import heapq
import math
from datetime import datetime
from typing import Any

from pydantic import TypeAdapter

from backend.domain.models import RoadEvent, Route

from .models import (
    GraphSnapshot,
    Identifier,
    Road,
    RoadStatus,
    RoutingCostConfig,
    SafeRoute,
    require_not_older,
)

_IDENTIFIER = TypeAdapter(Identifier)


class DynamicRoadGraph:
    """In-memory graph; each edge permits travel from source to target only.

    All mutations increment ``revision``. Routing reads the latest state and
    excludes blocked edges. Callers must supply their risk/time tradeoff.
    """

    def __init__(self, cost_config: RoutingCostConfig) -> None:
        self._cost_config = cost_config
        self._nodes: set[str] = set()
        self._roads: dict[str, Road] = {}
        self._outgoing: dict[str, set[str]] = {}
        self._revision = 0

    @property
    def revision(self) -> int:
        return self._revision

    def add_node(self, node_id: str) -> None:
        """Add an isolated node; adding one twice has no effect."""

        validated = _IDENTIFIER.validate_python(node_id)
        if validated not in self._nodes:
            self._nodes.add(validated)
            self._outgoing[validated] = set()
            self._revision += 1

    def add_road(self, road: Road) -> None:
        """Add one directed edge, creating its endpoint nodes if necessary."""

        if road.edge_id in self._roads:
            raise ValueError(f"road {road.edge_id!r} already exists")
        self._nodes.update((road.source_node_id, road.target_node_id))
        self._outgoing.setdefault(road.source_node_id, set()).add(road.edge_id)
        self._outgoing.setdefault(road.target_node_id, set())
        self._roads[road.edge_id] = road
        self._revision += 1

    def update_road(self, road: Road) -> None:
        """Replace an edge, including endpoints, unless its timestamp is stale."""

        previous = self._roads[road.edge_id]
        require_not_older(road.last_updated, previous.last_updated)
        if previous.source_node_id != road.source_node_id:
            self._outgoing[previous.source_node_id].remove(road.edge_id)
        self._nodes.update((road.source_node_id, road.target_node_id))
        self._outgoing.setdefault(road.source_node_id, set()).add(road.edge_id)
        self._outgoing.setdefault(road.target_node_id, set())
        self._roads[road.edge_id] = road
        self._revision += 1

    def remove_road(self, edge_id: str) -> Road:
        """Remove an edge and return its previous value; nodes remain present."""

        previous = self._roads.pop(edge_id)
        self._outgoing[previous.source_node_id].remove(edge_id)
        self._revision += 1
        return previous

    def _change_road(self, edge_id: str, updated_at: datetime, **changes: object) -> None:
        previous = self._roads[edge_id]
        require_not_older(updated_at, previous.last_updated)
        data: dict[str, Any] = previous.model_dump(mode="python")
        data.update(changes)
        data["last_updated"] = updated_at
        self.update_road(Road.model_validate(data))

    def block_road(self, edge_id: str, updated_at: datetime) -> None:
        self._change_road(edge_id, updated_at, status=RoadStatus.BLOCKED)

    def unblock_road(self, edge_id: str, updated_at: datetime) -> None:
        self._change_road(edge_id, updated_at, status=RoadStatus.OPEN)

    def update_risk(self, edge_id: str, risk_score: float, updated_at: datetime) -> None:
        self._change_road(edge_id, updated_at, risk_score=risk_score)

    def set_cost_config(self, config: RoutingCostConfig) -> None:
        """Change routing preferences for subsequent route queries."""

        if config != self._cost_config:
            self._cost_config = config
            self._revision += 1

    def get_road(self, edge_id: str) -> Road:
        return self._roads[edge_id]

    def get_state(self) -> GraphSnapshot:
        """Return a stable snapshot sorted by node and edge IDs."""

        return GraphSnapshot(
            revision=self._revision,
            node_ids=tuple(sorted(self._nodes)),
            roads=tuple(self._roads[edge_id] for edge_id in sorted(self._roads)),
            cost_config=self._cost_config,
        )

    def shortest_safe_route(
        self, origin_node_id: str, destination_node_id: str
    ) -> SafeRoute | None:
        """Find the minimum-cost feasible route, or None if unreachable.

        Equal-cost routes use lexicographically smaller edge-ID sequences, then
        node-ID sequences. The heap and adjacency iteration use explicit sorts
        so insertion order cannot change the result.
        """

        if origin_node_id not in self._nodes or destination_node_id not in self._nodes:
            return None
        if origin_node_id == destination_node_id:
            return SafeRoute(
                node_ids=(origin_node_id,),
                edge_ids=(),
                travel_time_seconds=0.0,
                distance_meters=0.0,
                effective_cost_seconds=0.0,
                risk_score_sum=0.0,
                graph_revision=self._revision,
            )

        # Heap tuple order implements the deterministic tie-break directly.
        queue: list[
            tuple[float, tuple[str, ...], tuple[str, ...], str, float, float, float]
        ] = [(0.0, (), (origin_node_id,), origin_node_id, 0.0, 0.0, 0.0)]
        best: dict[str, tuple[float, tuple[str, ...], tuple[str, ...]]] = {
            origin_node_id: (0.0, (), (origin_node_id,))
        }
        while queue:
            cost, edge_path, node_path, node_id, travel_time, distance, risk = heapq.heappop(
                queue
            )
            if (cost, edge_path, node_path) != best[node_id]:
                continue
            if node_id == destination_node_id:
                return SafeRoute(
                    node_ids=node_path,
                    edge_ids=edge_path,
                    travel_time_seconds=travel_time,
                    distance_meters=distance,
                    effective_cost_seconds=cost,
                    risk_score_sum=risk,
                    graph_revision=self._revision,
                )
            for edge_id in sorted(self._outgoing[node_id]):
                road = self._roads[edge_id]
                if road.status is RoadStatus.BLOCKED:
                    continue
                next_cost = cost + road.effective_cost_seconds(self._cost_config)
                next_travel = travel_time + road.effective_travel_time_seconds(
                    self._cost_config
                )
                next_distance = distance + road.distance_meters
                next_risk = risk + road.risk_score
                if not all(
                    math.isfinite(value)
                    for value in (next_cost, next_travel, next_distance, next_risk)
                ):
                    raise OverflowError("route totals exceed finite floating-point range")
                target = road.target_node_id
                next_edges = (*edge_path, edge_id)
                next_nodes = (*node_path, target)
                candidate = (next_cost, next_edges, next_nodes)
                if target not in best or candidate < best[target]:
                    best[target] = candidate
                    heapq.heappush(
                        queue,
                        (
                            next_cost,
                            next_edges,
                            next_nodes,
                            target,
                            next_travel,
                            next_distance,
                            next_risk,
                        ),
                    )
        return None

    def recompute_route(
        self, previous_route: SafeRoute, current_node_id: str | None = None
    ) -> SafeRoute | None:
        """Reroute from a graph node to the prior destination using current roads."""

        origin = previous_route.node_ids[0] if current_node_id is None else current_node_id
        return self.shortest_safe_route(origin, previous_route.node_ids[-1])

    def find_route(self, origin_node_id: str, destination_node_id: str) -> Route | None:
        """Compatibility adapter for the existing dispatch RoadNetwork protocol."""

        safe = self.shortest_safe_route(origin_node_id, destination_node_id)
        if safe is None:
            return None
        return Route(
            node_ids=list(safe.node_ids),
            edge_ids=list(safe.edge_ids),
            travel_time_seconds=safe.travel_time_seconds,
            distance_meters=safe.distance_meters,
        )

    def apply_event(self, event: RoadEvent) -> None:
        """Apply the legacy blocked/unblocked event contract."""

        if event.blocked:
            self.block_road(event.edge_id, event.occurred_at)
        else:
            self.unblock_road(event.edge_id, event.occurred_at)
