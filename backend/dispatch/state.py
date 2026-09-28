"""Build a single comparable eligibility and route snapshot for all policies."""

import math
from collections.abc import Sequence
from datetime import datetime

from backend.routing import DynamicRoadGraph

from .models import DispatchRequest, DispatchState, DispatchTeam, FeasiblePair


def build_dispatch_state(
    scenario_id: str,
    decision_time: datetime,
    requests: Sequence[DispatchRequest],
    teams: Sequence[DispatchTeam],
    graph: DynamicRoadGraph,
    weight_policy_version: str,
) -> DispatchState:
    """Freeze reviewed inputs and all feasible paths on one graph revision.

    A missing accepted incident anchor, unavailable team, unverified required
    capability, or unreachable route creates no eligible pair. Route travel
    time is rounded upward to whole seconds only for the integer assignment
    objective; the actual route time is retained for arrival estimates.
    """

    revision = graph.revision
    ordered_requests = tuple(sorted(requests, key=lambda item: item.request_id))
    ordered_teams = tuple(sorted(teams, key=lambda item: item.team_id))
    pairs: list[FeasiblePair] = []
    for request in ordered_requests:
        if not request.routing_anchor_accepted or request.incident_node_id is None:
            continue
        for team in ordered_teams:
            if not team.available or not request.required_capabilities.issubset(
                team.capabilities
            ):
                continue
            route = graph.shortest_safe_route(team.graph_node_id, request.incident_node_id)
            if route is None:
                continue
            pairs.append(
                FeasiblePair(
                    request_id=request.request_id,
                    team_id=team.team_id,
                    route=route,
                    eta_seconds=math.ceil(route.travel_time_seconds),
                )
            )
    if graph.revision != revision:
        raise RuntimeError("road graph changed while dispatch eligibility was built")
    return DispatchState(
        scenario_id=scenario_id,
        decision_time=decision_time,
        graph_revision=revision,
        weight_policy_version=weight_policy_version,
        requests=ordered_requests,
        teams=ordered_teams,
        feasible_pairs=tuple(pairs),
    )
