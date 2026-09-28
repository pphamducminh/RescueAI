"""Deterministic one-wave baselines and exact weighted assignment optimizer."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import timedelta
from types import MappingProxyType

from .contracts import DispatchStrategy
from .models import (
    DispatchAssignment,
    DispatchPlan,
    DispatchRequest,
    DispatchState,
    FeasiblePair,
    ObjectiveValue,
    PlanStatus,
    UnservedReason,
    UnservedRequest,
)


def _candidates(state: DispatchState) -> tuple[DispatchRequest, ...]:
    return tuple(request for request in state.requests if request.dispatch_weight is not None)


def _dominance_constant(state: DispatchState) -> int:
    """K = 1 + sum(w_i * maximum feasible whole-second ETA for i)."""

    maximum_eta: dict[str, int] = {}
    for pair in state.feasible_pairs:
        maximum_eta[pair.request_id] = max(maximum_eta.get(pair.request_id, 0), pair.eta_seconds)
    return 1 + sum(
        request.dispatch_weight * maximum_eta.get(request.request_id, 0)
        for request in _candidates(state)
        if request.dispatch_weight is not None
    )


def _plan(
    state: DispatchState,
    strategy_name: str,
    selected: Mapping[str, FeasiblePair],
    explanation: str,
    solver_status: str,
) -> DispatchPlan:
    requests = {request.request_id: request for request in state.requests}
    candidate = {request.request_id: request for request in _candidates(state)}
    pair_ids = {(pair.request_id, pair.team_id) for pair in state.feasible_pairs}
    if not set(selected).issubset(candidate):
        raise ValueError("strategy selected a request without an approved weight")
    if len({pair.team_id for pair in selected.values()}) != len(selected):
        raise ValueError("strategy assigned one team more than once")
    if any(request_id != pair.request_id for request_id, pair in selected.items()):
        raise ValueError("selected pair request ID does not match its key")
    if any((request_id, pair.team_id) not in pair_ids for request_id, pair in selected.items()):
        raise ValueError("strategy selected a pair outside the feasible snapshot")
    assignments: list[DispatchAssignment] = []
    for request_id, pair in sorted(selected.items()):
        request = requests[request_id]
        route_seconds = pair.route.travel_time_seconds
        assignments.append(
            DispatchAssignment(
                request_id=request_id,
                team_id=pair.team_id,
                route=pair.route,
                eta_seconds=pair.eta_seconds,
                predicted_arrival_at=state.decision_time + timedelta(seconds=route_seconds),
                predicted_response_seconds=(
                    state.decision_time - request.received_at
                ).total_seconds()
                + route_seconds,
                explanation=explanation,
            )
        )
    unserved: list[UnservedRequest] = []
    feasible_by_request = {pair.request_id for pair in state.feasible_pairs}
    for request in sorted(state.requests, key=lambda item: item.request_id):
        if request.request_id in selected:
            continue
        reasons: list[UnservedReason] = []
        if not request.routing_anchor_accepted:
            reasons.append(UnservedReason.LOCATION_PENDING_REVIEW)
        if request.dispatch_weight is None:
            reasons.append(UnservedReason.WEIGHT_PENDING_REVIEW)
        if request.routing_anchor_accepted and request.request_id not in feasible_by_request:
            reasons.append(UnservedReason.NO_ELIGIBLE_ROUTE)
        if not reasons:
            reasons.append(UnservedReason.CAPACITY_SATURATED)
        unserved.append(UnservedRequest(request_id=request.request_id, reasons=tuple(reasons)))

    pending_loss = sum(
        request.dispatch_weight
        for request_id, request in candidate.items()
        if request_id not in selected and request.dispatch_weight is not None
    )
    weighted_travel = 0
    for request_id, pair in selected.items():
        weight = candidate[request_id].dispatch_weight
        assert weight is not None
        weighted_travel += weight * pair.eta_seconds
    dominance = _dominance_constant(state)
    return DispatchPlan(
        strategy_name=strategy_name,
        scenario_id=state.scenario_id,
        graph_revision=state.graph_revision,
        status=PlanStatus.AWAITING_HUMAN_APPROVAL,
        assignments=tuple(assignments),
        unserved_requests=tuple(unserved),
        objective_value=ObjectiveValue(
            weighted_pending_loss=pending_loss,
            weighted_travel_seconds=weighted_travel,
            dominance_constant=dominance,
            scalar_cost=dominance * pending_loss + weighted_travel,
        ),
        explanation_metadata={
            "rule": explanation,
            "solver_status": solver_status,
            "solver_backend": (
                "python_integer_hungarian" if solver_status == "OPTIMAL" else "deterministic_greedy"
            ),
            "weight_policy_version": state.weight_policy_version,
            "planning_eta_rule": "ceil_route_travel_time_to_whole_seconds",
            "candidate_requests": len(candidate),
            "weight_pending_review": len(state.requests) - len(candidate),
            "human_approval_required": True,
        },
    )


class FCFS:
    @property
    def name(self) -> str:
        return "fcfs"

    def solve(self, state: DispatchState) -> DispatchPlan:
        available = {team.team_id for team in state.teams if team.available}
        by_request: dict[str, list[FeasiblePair]] = {}
        for pair in state.feasible_pairs:
            by_request.setdefault(pair.request_id, []).append(pair)
        selected: dict[str, FeasiblePair] = {}
        for request in sorted(
            _candidates(state), key=lambda item: (item.received_at, item.request_id)
        ):
            choices = [
                pair for pair in by_request.get(request.request_id, ()) if pair.team_id in available
            ]
            if choices:
                best = min(choices, key=lambda pair: (pair.eta_seconds, pair.team_id))
                selected[request.request_id] = best
                available.remove(best.team_id)
        return _plan(state, self.name, selected, "oldest request then fastest free team", "GREEDY")


class NearestTeam:
    @property
    def name(self) -> str:
        return "nearest_team"

    def solve(self, state: DispatchState) -> DispatchPlan:
        candidate_ids = {request.request_id for request in _candidates(state)}
        available = {team.team_id for team in state.teams if team.available}
        selected: dict[str, FeasiblePair] = {}
        for pair in sorted(
            state.feasible_pairs,
            key=lambda item: (item.eta_seconds, item.request_id, item.team_id),
        ):
            if (
                pair.request_id in candidate_ids
                and pair.request_id not in selected
                and pair.team_id in available
            ):
                selected[pair.request_id] = pair
                available.remove(pair.team_id)
        return _plan(state, self.name, selected, "globally shortest feasible pair first", "GREEDY")


class PriorityAwareGreedy:
    @property
    def name(self) -> str:
        return "priority_aware_greedy"

    def solve(self, state: DispatchState) -> DispatchPlan:
        available = {team.team_id for team in state.teams if team.available}
        by_request: dict[str, list[FeasiblePair]] = {}
        for pair in state.feasible_pairs:
            by_request.setdefault(pair.request_id, []).append(pair)
        selected: dict[str, FeasiblePair] = {}
        for request in sorted(
            _candidates(state),
            key=lambda item: (
                -item.dispatch_weight if item.dispatch_weight is not None else 0,
                item.received_at,
                item.request_id,
            ),
        ):
            choices = [
                pair for pair in by_request.get(request.request_id, ()) if pair.team_id in available
            ]
            if choices:
                best = min(choices, key=lambda pair: (pair.eta_seconds, pair.team_id))
                selected[request.request_id] = best
                available.remove(best.team_id)
        return _plan(
            state, self.name, selected, "highest approved weight then fastest free team", "GREEDY"
        )


def _minimum_cost_columns(costs: list[list[int]]) -> list[int]:
    """Exact rectangular Hungarian assignment with stable column scanning."""

    row_count = len(costs)
    if row_count == 0:
        return []
    column_count = len(costs[0])
    if column_count < row_count or any(len(row) != column_count for row in costs):
        raise ValueError("assignment needs at least one distinct column per request")
    infinity = row_count * max(max(row) for row in costs) + 1
    row_potential = [0] * (row_count + 1)
    column_potential = [0] * (column_count + 1)
    matched_row = [0] * (column_count + 1)
    predecessor = [0] * (column_count + 1)
    for row_index in range(1, row_count + 1):
        matched_row[0] = row_index
        column = 0
        slack = [infinity] * (column_count + 1)
        used = [False] * (column_count + 1)
        while True:
            used[column] = True
            current_row = matched_row[column]
            delta = infinity
            next_column = 0
            for candidate_column in range(1, column_count + 1):
                if used[candidate_column]:
                    continue
                reduced = (
                    costs[current_row - 1][candidate_column - 1]
                    - row_potential[current_row]
                    - column_potential[candidate_column]
                )
                if reduced < slack[candidate_column]:
                    slack[candidate_column] = reduced
                    predecessor[candidate_column] = column
                if slack[candidate_column] < delta:
                    delta = slack[candidate_column]
                    next_column = candidate_column
            for candidate_column in range(column_count + 1):
                if used[candidate_column]:
                    row_potential[matched_row[candidate_column]] += delta
                    column_potential[candidate_column] -= delta
                else:
                    slack[candidate_column] -= delta
            column = next_column
            if matched_row[column] == 0:
                break
        while True:
            previous_column = predecessor[column]
            matched_row[column] = matched_row[previous_column]
            column = previous_column
            if column == 0:
                break
    assignment = [-1] * row_count
    for column in range(1, column_count + 1):
        if matched_row[column]:
            assignment[matched_row[column] - 1] = column - 1
    return assignment


class RescueAIOptimizer:
    """Exact P-then-Q matching using pending columns and derived dominance K."""

    @property
    def name(self) -> str:
        return "rescueai_optimizer"

    def solve(self, state: DispatchState) -> DispatchPlan:
        requests = sorted(_candidates(state), key=lambda item: item.request_id)
        teams = sorted(
            (team for team in state.teams if team.available), key=lambda item: item.team_id
        )
        if not requests:
            return _plan(state, self.name, {}, "exact weighted P then Q assignment", "OPTIMAL")
        pair_by_id = {(pair.request_id, pair.team_id): pair for pair in state.feasible_pairs}
        dominance = _dominance_constant(state)
        total_weight = sum(
            request.dispatch_weight for request in requests if request.dispatch_weight is not None
        )
        all_pending_cost = dominance * total_weight
        forbidden_cost = all_pending_cost + 1
        costs: list[list[int]] = []
        for request in requests:
            assert request.dispatch_weight is not None
            row = [
                request.dispatch_weight * pair_by_id[(request.request_id, team.team_id)].eta_seconds
                if (request.request_id, team.team_id) in pair_by_id
                else forbidden_cost
                for team in teams
            ]
            row.extend([dominance * request.dispatch_weight] * len(requests))
            costs.append(row)
        columns = _minimum_cost_columns(costs)
        selected: dict[str, FeasiblePair] = {}
        for request, column in zip(requests, columns, strict=True):
            if column < len(teams):
                pair = pair_by_id.get((request.request_id, teams[column].team_id))
                if pair is None:
                    raise RuntimeError("exact assignment selected a forbidden team/request pair")
                selected[request.request_id] = pair
        return _plan(state, self.name, selected, "exact weighted P then Q assignment", "OPTIMAL")


_STRATEGY_FACTORIES: Mapping[str, Callable[[], DispatchStrategy]] = MappingProxyType(
    {
        "fcfs": FCFS,
        "nearest_team": NearestTeam,
        "priority_aware_greedy": PriorityAwareGreedy,
        "rescueai_optimizer": RescueAIOptimizer,
    }
)


def list_strategies() -> tuple[str, ...]:
    return tuple(_STRATEGY_FACTORIES)


def get_strategy(name: str) -> DispatchStrategy:
    """Construct a fresh stateless strategy from the immutable registry."""

    try:
        return _STRATEGY_FACTORIES[name]()
    except KeyError as exc:
        raise ValueError(f"unknown dispatch strategy {name!r}") from exc
