"""Contract tests for the four deterministic, single-wave dispatch strategies."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import product
from random import Random

import pytest
from pydantic import ValidationError

from backend.dispatch import (
    FCFS,
    DispatchPlan,
    DispatchRequest,
    DispatchState,
    DispatchTeam,
    FeasiblePair,
    NearestTeam,
    PriorityAwareGreedy,
    RescueAIOptimizer,
    UnservedReason,
    build_dispatch_state,
    get_strategy,
    list_strategies,
)
from backend.routing import DynamicRoadGraph, Road, RoadStatus, RoutingCostConfig, SafeRoute

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
REVISION = 7


def _request(
    request_id: str,
    *,
    minutes_waiting: int = 0,
    weight: int | None = 1,
    evaluation_weight: int = 1,
    incident_node_id: str | None = "",
    routing_anchor_accepted: bool = True,
    required_capabilities: frozenset[str] = frozenset(),
    critical_cue: bool = False,
) -> DispatchRequest:
    return DispatchRequest(
        request_id=request_id,
        received_at=NOW - timedelta(minutes=minutes_waiting),
        incident_node_id=(incident_node_id or request_id) if incident_node_id is not None else None,
        routing_anchor_accepted=routing_anchor_accepted,
        required_capabilities=required_capabilities,
        dispatch_weight=weight,
        evaluation_weight=evaluation_weight,
        critical_cue=critical_cue,
    )


def _team(
    team_id: str,
    *,
    available: bool = True,
    capabilities: frozenset[str] = frozenset(),
) -> DispatchTeam:
    return DispatchTeam(
        team_id=team_id,
        graph_node_id=team_id,
        available=available,
        capabilities=capabilities,
    )


def _pair(team_id: str, request_id: str, eta_seconds: int) -> FeasiblePair:
    return FeasiblePair(
        request_id=request_id,
        team_id=team_id,
        route=SafeRoute(
            node_ids=(team_id, request_id),
            edge_ids=(f"{team_id}-{request_id}",),
            travel_time_seconds=float(eta_seconds),
            distance_meters=float(eta_seconds),
            effective_cost_seconds=float(eta_seconds),
            risk_score_sum=0.0,
            graph_revision=REVISION,
        ),
        eta_seconds=eta_seconds,
    )


def _state(
    requests: tuple[DispatchRequest, ...],
    teams: tuple[DispatchTeam, ...],
    pairs: tuple[FeasiblePair, ...],
) -> DispatchState:
    return DispatchState(
        scenario_id="scenario-test",
        decision_time=NOW,
        graph_revision=REVISION,
        weight_policy_version="test-v1",
        requests=requests,
        teams=teams,
        feasible_pairs=pairs,
    )


def _assignment_pairs(plan: DispatchPlan) -> set[tuple[str, str]]:
    return {(a.request_id, a.team_id) for a in plan.assignments}


def _unserved_ids(plan: DispatchPlan) -> set[str]:
    return {r.request_id for r in plan.unserved_requests}


def _worked_example() -> DispatchState:
    return _state(
        (
            _request("C", minutes_waiting=10, weight=5, critical_cue=True),
            _request("O", minutes_waiting=5, weight=1),
        ),
        (_team("A"), _team("B")),
        (
            _pair("A", "C", 180),
            _pair("A", "O", 240),
            _pair("B", "C", 300),
            _pair("B", "O", 2400),
        ),
    )


def _road(edge_id: str, source: str, target: str, seconds: float) -> Road:
    return Road(
        edge_id=edge_id,
        source_node_id=source,
        target_node_id=target,
        distance_meters=seconds,
        base_travel_time_seconds=seconds,
        risk_score=0.0,
        status=RoadStatus.OPEN,
        last_updated=NOW,
    )


def _graph() -> DynamicRoadGraph:
    return DynamicRoadGraph(
        RoutingCostConfig(risk_penalty_seconds_per_unit=0.0, degraded_time_multiplier=2.0)
    )


@pytest.mark.parametrize(
    ("strategy_type", "pairs", "weighted_travel"),
    [
        (FCFS, {("C", "A"), ("O", "B")}, 3300),
        (NearestTeam, {("C", "A"), ("O", "B")}, 3300),
        (PriorityAwareGreedy, {("C", "A"), ("O", "B")}, 3300),
        (RescueAIOptimizer, {("C", "B"), ("O", "A")}, 1740),
    ],
)
def test_approved_worked_example(
    strategy_type: (
        type[FCFS] | type[NearestTeam] | type[PriorityAwareGreedy] | type[RescueAIOptimizer]
    ),
    pairs: set[tuple[str, str]],
    weighted_travel: int,
) -> None:
    state = _worked_example()
    plan = strategy_type().solve(state)
    assert _assignment_pairs(plan) == pairs
    assert _unserved_ids(plan) == set()
    assert plan.objective_value.weighted_pending_loss == 0
    assert plan.objective_value.weighted_travel_seconds == weighted_travel
    assert plan.objective_value.scalar_cost == weighted_travel
    assert plan.status == "awaiting_human_approval"
    assert {a.route.graph_revision for a in plan.assignments} == {REVISION}
    assert {a.predicted_response_seconds - a.eta_seconds for a in plan.assignments} == {
        600,
        300,
    }


def test_coverage_objective_precedes_travel_with_one_team() -> None:
    state = _state(
        (
            _request("old", minutes_waiting=10, weight=1),
            _request("near", minutes_waiting=5, weight=1),
            _request("urgent", minutes_waiting=1, weight=5, critical_cue=True),
        ),
        (_team("A"),),
        (
            _pair("A", "old", 30),
            _pair("A", "near", 20),
            _pair("A", "urgent", 100),
        ),
    )
    assert _assignment_pairs(FCFS().solve(state)) == {("old", "A")}
    assert _assignment_pairs(NearestTeam().solve(state)) == {("near", "A")}
    assert _assignment_pairs(PriorityAwareGreedy().solve(state)) == {("urgent", "A")}
    optimized = RescueAIOptimizer().solve(state)
    assert _assignment_pairs(optimized) == {("urgent", "A")}
    assert _unserved_ids(optimized) == {"old", "near"}
    assert optimized.objective_value.weighted_pending_loss == 2
    assert optimized.objective_value.weighted_travel_seconds == 500


def test_optimizer_matches_independent_exhaustive_small_case_oracle() -> None:
    rng = Random(412073)
    for scenario_number in range(24):
        request_count = rng.randint(2, 4)
        team_count = rng.randint(1, 4)
        requests = tuple(
            _request(f"R{i}", weight=rng.randint(1, 5)) for i in range(request_count)
        )
        teams = tuple(_team(f"T{j}") for j in range(team_count))
        eta_by_pair: dict[tuple[str, str], int] = {}
        for request in requests:
            for team in teams:
                if rng.random() < 0.7:
                    eta_by_pair[(request.request_id, team.team_id)] = rng.randint(1, 500)
        pairs = tuple(
            _pair(team_id, request_id, eta)
            for (request_id, team_id), eta in eta_by_pair.items()
        )
        state = _state(requests, teams, pairs)
        planned = RescueAIOptimizer().solve(state)
        planned_cost = (
            planned.objective_value.weighted_pending_loss,
            planned.objective_value.weighted_travel_seconds,
        )

        options = tuple(
            (
                None,
                *(
                    team.team_id
                    for team in teams
                    if (request.request_id, team.team_id) in eta_by_pair
                ),
            )
            for request in requests
        )
        oracle_cost: tuple[int, int] | None = None
        for chosen_teams in product(*options):
            assigned_teams = [team_id for team_id in chosen_teams if team_id is not None]
            if len(set(assigned_teams)) != len(assigned_teams):
                continue
            pending_loss = 0
            weighted_travel = 0
            for request, team_id in zip(requests, chosen_teams, strict=True):
                assert request.dispatch_weight is not None
                if team_id is None:
                    pending_loss += request.dispatch_weight
                else:
                    weighted_travel += (
                        request.dispatch_weight * eta_by_pair[(request.request_id, team_id)]
                    )
            candidate = (pending_loss, weighted_travel)
            if oracle_cost is None or candidate < oracle_cost:
                oracle_cost = candidate
        assert planned_cost == oracle_cost, f"scenario {scenario_number}"


def test_state_builder_enforces_accepted_anchor_capability_and_availability() -> None:
    graph = _graph()
    graph.add_road(_road("A-R", "A", "R", 60.0))
    graph.add_node("unreachable")
    requests = (
        _request("R", required_capabilities=frozenset({"boat"})),
        _request("gps-only", incident_node_id=None, routing_anchor_accepted=False),
        _request("unreachable", incident_node_id="unreachable"),
    )
    teams = (
        _team("A", capabilities=frozenset({"boat"})),
        _team("B", capabilities=frozenset()),
        _team("C", available=False, capabilities=frozenset({"boat"})),
    )
    state = build_dispatch_state(
        "eligibility-test", NOW, requests, teams, graph, "reviewed-weights-v1"
    )
    assert {(pair.request_id, pair.team_id) for pair in state.feasible_pairs} == {("R", "A")}
    plan = RescueAIOptimizer().solve(state)
    assert _assignment_pairs(plan) == {("R", "A")}
    reasons = {item.request_id: item.reasons for item in plan.unserved_requests}
    assert UnservedReason.LOCATION_PENDING_REVIEW in reasons["gps-only"]
    assert UnservedReason.NO_ELIGIBLE_ROUTE in reasons["unreachable"]


def test_graph_replan_uses_new_revision_and_avoids_blocked_bridge() -> None:
    graph = _graph()
    graph.add_road(_road("direct", "A", "R", 5.0))
    graph.add_road(_road("detour-1", "A", "X", 4.0))
    graph.add_road(_road("detour-2", "X", "R", 4.0))
    request = _request("R")
    team = _team("A")
    first = build_dispatch_state("replan", NOW, (request,), (team,), graph, "v1")
    assert first.feasible_pairs[0].eta_seconds == 5
    assert first.feasible_pairs[0].route.edge_ids == ("direct",)

    graph.block_road("direct", NOW + timedelta(seconds=1))
    updated = build_dispatch_state("replan", NOW, (request,), (team,), graph, "v1")
    assert updated.graph_revision > first.graph_revision
    assert updated.feasible_pairs[0].eta_seconds == 8
    assert updated.feasible_pairs[0].route.edge_ids == ("detour-1", "detour-2")
    plan = RescueAIOptimizer().solve(updated)
    assert plan.assignments[0].route.graph_revision == updated.graph_revision
    assert "direct" not in plan.assignments[0].route.edge_ids


def test_state_rejects_feasible_pair_without_accepted_anchor() -> None:
    with pytest.raises(ValidationError):
        _state(
            (_request("R", routing_anchor_accepted=False),),
            (_team("A"),),
            (_pair("A", "R", 20),),
        )


@pytest.mark.parametrize(
    "strategy_type", [FCFS, NearestTeam, PriorityAwareGreedy, RescueAIOptimizer]
)
def test_no_feasible_path_keeps_request_visible(strategy_type: type) -> None:
    state = _state((_request("isolated"),), (_team("A"),), ())
    plan = strategy_type().solve(state)
    assert plan.assignments == ()
    assert _unserved_ids(plan) == {"isolated"}
    assert plan.objective_value.weighted_pending_loss == 1
    assert plan.objective_value.weighted_travel_seconds == 0


@pytest.mark.parametrize(
    "strategy_type", [FCFS, NearestTeam, PriorityAwareGreedy, RescueAIOptimizer]
)
def test_missing_reviewed_weight_is_not_invented(strategy_type: type) -> None:
    state = _state((_request("pending", weight=None),), (_team("A"),), (_pair("A", "pending", 20),))
    plan = strategy_type().solve(state)
    assert plan.assignments == ()
    assert _unserved_ids(plan) == {"pending"}
    assert plan.unserved_requests[0].reasons == (UnservedReason.WEIGHT_PENDING_REVIEW,)


def test_equal_cost_tie_and_input_order_are_deterministic() -> None:
    requests = (_request("R1"), _request("R2"))
    teams = (_team("A"), _team("B"))
    pairs = tuple(_pair(team, request, 100) for team in ("B", "A") for request in ("R2", "R1"))
    for strategy_type in (FCFS, NearestTeam, PriorityAwareGreedy, RescueAIOptimizer):
        first = strategy_type().solve(_state(requests, teams, pairs))
        reversed_input = strategy_type().solve(
            _state(tuple(reversed(requests)), tuple(reversed(teams)), tuple(reversed(pairs)))
        )
        assert first.model_dump(mode="json") == reversed_input.model_dump(mode="json")
        assert {request_id for request_id, _ in _assignment_pairs(first)} == {"R1", "R2"}
        assert {team_id for _, team_id in _assignment_pairs(first)} == {"A", "B"}


def test_solving_does_not_mutate_input_state() -> None:
    state = _worked_example()
    before = state.model_dump_json()
    for strategy_type in (FCFS, NearestTeam, PriorityAwareGreedy, RescueAIOptimizer):
        strategy_type().solve(state)
    assert state.model_dump_json() == before


def test_strategy_registry_exposes_all_algorithms() -> None:
    names = set(list_strategies())
    assert names == {"fcfs", "nearest_team", "priority_aware_greedy", "rescueai_optimizer"}
    assert isinstance(get_strategy("fcfs"), FCFS)
    assert isinstance(get_strategy("nearest_team"), NearestTeam)
    assert isinstance(get_strategy("priority_aware_greedy"), PriorityAwareGreedy)
    assert isinstance(get_strategy("rescueai_optimizer"), RescueAIOptimizer)
    with pytest.raises(ValueError, match="unknown dispatch strategy"):
        get_strategy("not_a_strategy")


@pytest.mark.parametrize("invalid_weight", [0, -1, 1.5, True])
def test_dispatch_weight_requires_positive_integer(invalid_weight: object) -> None:
    with pytest.raises(ValidationError):
        _request("R", weight=invalid_weight)  # type: ignore[arg-type]


def test_request_receipt_time_must_be_timezone_aware() -> None:
    with pytest.raises(ValidationError):
        DispatchRequest(
            request_id="R",
            received_at=NOW.replace(tzinfo=None),
            incident_node_id="R",
            routing_anchor_accepted=True,
            required_capabilities=frozenset(),
            dispatch_weight=1,
            evaluation_weight=1,
            critical_cue=False,
        )
