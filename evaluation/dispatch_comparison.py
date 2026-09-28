"""Compare dispatch strategies on identical seeded, synthetic one-wave scenarios.

Every strategy receives the same immutable eligibility/routing snapshot. The
experiment assumes immediate *simulated* dispatcher approval and arrival at
the planned ETA; it does not model travel progress, later waves, or outcomes.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from backend.dispatch import (
    DispatchPlan,
    DispatchRequest,
    DispatchState,
    DispatchTeam,
    build_dispatch_state,
    get_strategy,
    list_strategies,
)
from backend.routing import DynamicRoadGraph, Road, RoadStatus, RoutingCostConfig

DEFAULT_OUTPUT_PREFIX = Path("evaluation/results/dispatch_comparison")
BASE_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
GENERATOR_VERSION = "synthetic_dispatch_one_wave_v1"
WEIGHT_POLICY_VERSION = "synthetic_reviewed_dispatch_weight_v1"
HORIZON_SECONDS = 3600
ROUTING_CONFIG = RoutingCostConfig(
    risk_penalty_seconds_per_unit=20.0,
    degraded_time_multiplier=1.5,
)


@dataclass(frozen=True)
class SyntheticScenario:
    """A fixed state and its disclosed generator settings."""

    state: DispatchState
    design: str
    origin: str


def _road(
    graph: DynamicRoadGraph,
    *,
    scenario_id: str,
    team_id: str,
    request_id: str,
    seconds: int,
    status: RoadStatus = RoadStatus.OPEN,
    risk_score: float = 0.0,
) -> None:
    graph.add_road(
        Road(
            edge_id=f"{scenario_id}:{team_id}->{request_id}",
            source_node_id=f"node:{team_id}",
            target_node_id=f"node:{request_id}",
            distance_meters=float(seconds * 8),
            base_travel_time_seconds=float(seconds),
            risk_score=risk_score,
            status=status,
            last_updated=BASE_TIME,
        )
    )


def _request(
    request_id: str,
    *,
    seconds_waiting: int,
    dispatch_weight: int | None,
    evaluation_weight: int,
    critical_cue: bool,
    anchor_accepted: bool = True,
    required_capabilities: frozenset[str] = frozenset({"basic"}),
) -> DispatchRequest:
    return DispatchRequest(
        request_id=request_id,
        received_at=BASE_TIME - timedelta(seconds=seconds_waiting),
        incident_node_id=f"node:{request_id}",
        routing_anchor_accepted=anchor_accepted,
        required_capabilities=required_capabilities,
        dispatch_weight=dispatch_weight,
        evaluation_weight=evaluation_weight,
        critical_cue=critical_cue,
    )


def _team(
    team_id: str,
    *,
    available: bool = True,
    capabilities: frozenset[str] = frozenset({"basic"}),
) -> DispatchTeam:
    return DispatchTeam(
        team_id=team_id,
        graph_node_id=f"node:{team_id}",
        available=available,
        capabilities=capabilities,
    )


def _worked_example() -> SyntheticScenario:
    """The approved assignment-model example, expressed in integer seconds."""

    scenario_id = "synthetic-0000"
    graph = DynamicRoadGraph(ROUTING_CONFIG)
    for team_id, request_id, seconds in (
        ("team_a", "critical", 180),
        ("team_a", "other", 240),
        ("team_b", "critical", 300),
        ("team_b", "other", 2400),
    ):
        _road(
            graph,
            scenario_id=scenario_id,
            team_id=team_id,
            request_id=request_id,
            seconds=seconds,
        )
    state = build_dispatch_state(
        scenario_id=scenario_id,
        decision_time=BASE_TIME,
        requests=(
            _request(
                "critical",
                seconds_waiting=120,
                dispatch_weight=5,
                evaluation_weight=5,
                critical_cue=True,
            ),
            _request(
                "other",
                seconds_waiting=60,
                dispatch_weight=1,
                evaluation_weight=1,
                critical_cue=False,
            ),
        ),
        teams=(_team("team_a"), _team("team_b")),
        graph=graph,
        weight_policy_version=WEIGHT_POLICY_VERSION,
    )
    return SyntheticScenario(
        state=state,
        design="approved_2x2_assignment_example",
        origin="hand_constructed_control",
    )


def _constrained_example() -> SyntheticScenario:
    """Exercise pending reasons: capacity, blocked route, anchor, and weight."""

    scenario_id = "synthetic-0001"
    graph = DynamicRoadGraph(ROUTING_CONFIG)
    for request_id, seconds, status in (
        ("older", 600, RoadStatus.OPEN),
        ("nearer", 100, RoadStatus.OPEN),
        ("blocked", 90, RoadStatus.BLOCKED),
        ("anchor_review", 75, RoadStatus.OPEN),
        ("weight_review", 80, RoadStatus.OPEN),
    ):
        _road(
            graph,
            scenario_id=scenario_id,
            team_id="team_a",
            request_id=request_id,
            seconds=seconds,
            status=status,
        )
    state = build_dispatch_state(
        scenario_id=scenario_id,
        decision_time=BASE_TIME,
        requests=(
            _request(
                "older",
                seconds_waiting=300,
                dispatch_weight=5,
                evaluation_weight=5,
                critical_cue=True,
            ),
            _request(
                "nearer",
                seconds_waiting=200,
                dispatch_weight=1,
                evaluation_weight=1,
                critical_cue=False,
            ),
            _request(
                "blocked",
                seconds_waiting=150,
                dispatch_weight=3,
                evaluation_weight=3,
                critical_cue=True,
            ),
            _request(
                "anchor_review",
                seconds_waiting=100,
                dispatch_weight=2,
                evaluation_weight=2,
                critical_cue=False,
                anchor_accepted=False,
            ),
            _request(
                "weight_review",
                seconds_waiting=50,
                dispatch_weight=None,
                evaluation_weight=4,
                critical_cue=True,
            ),
        ),
        teams=(_team("team_a"),),
        graph=graph,
        weight_policy_version=WEIGHT_POLICY_VERSION,
    )
    return SyntheticScenario(
        state=state,
        design="constrained_review_and_blockage",
        origin="hand_constructed_control",
    )


def _random_scenario(index: int, random_source: random.Random) -> SyntheticScenario:
    scenario_id = f"synthetic-{index:04d}"
    graph = DynamicRoadGraph(ROUTING_CONFIG)
    team_count = random_source.randint(2, 3)
    request_count = team_count + random_source.randint(1, 2)
    teams = tuple(
        _team(
            f"team_{team_index}",
            available=(team_index == 0 or random_source.random() >= 0.15),
            capabilities=(
                frozenset({"basic", "water"})
                if team_index == 0 or random_source.random() < 0.35
                else frozenset({"basic"})
            ),
        )
        for team_index in range(team_count)
    )
    requests = tuple(
        _request(
            f"request_{request_index}",
            seconds_waiting=random_source.randint(0, 600),
            dispatch_weight=(
                None if request_index > 0 and random_source.random() < 0.08
                else random_source.randint(1, 5)
            ),
            evaluation_weight=random_source.randint(1, 5),
            critical_cue=random_source.random() < 0.4,
            anchor_accepted=(request_index == 0 or random_source.random() >= 0.1),
            required_capabilities=(
                frozenset({"basic", "water"})
                if request_index > 0 and random_source.random() < 0.25
                else frozenset({"basic"})
            ),
        )
        for request_index in range(request_count)
    )
    for team in teams:
        for request in requests:
            _road(
                graph,
                scenario_id=scenario_id,
                team_id=team.team_id,
                request_id=request.request_id,
                seconds=random_source.randint(60, 900),
                status=(
                    RoadStatus.BLOCKED if random_source.random() < 0.18
                    else RoadStatus.DEGRADED if random_source.random() < 0.12
                    else RoadStatus.OPEN
                ),
                risk_score=round(random_source.random(), 6),
            )
    state = build_dispatch_state(
        scenario_id=scenario_id,
        decision_time=BASE_TIME,
        requests=requests,
        teams=teams,
        graph=graph,
        weight_policy_version=WEIGHT_POLICY_VERSION,
    )
    return SyntheticScenario(
        state=state,
        design="seeded_directed_pair_graph",
        origin="seeded_generated",
    )


def generate_scenarios(*, seed: int, count: int) -> tuple[SyntheticScenario, ...]:
    """Use a local PRNG; no scenario or graph state is shared across calls."""

    if count < 1:
        raise ValueError("scenario count must be positive")
    random_source = random.Random(seed)
    scenarios = [_worked_example()]
    if count >= 2:
        scenarios.append(_constrained_example())
    scenarios.extend(_random_scenario(index, random_source) for index in range(2, count))
    return tuple(scenarios)


def generate_generated_scenarios(*, seed: int, count: int) -> tuple[SyntheticScenario, ...]:
    """Generate only seeded cases, without the two fixed diagnostic controls."""

    if count < 1:
        raise ValueError("scenario count must be positive")
    random_source = random.Random(seed)
    return tuple(_random_scenario(index, random_source) for index in range(count))


def _validate_plan(state: DispatchState, plan: DispatchPlan) -> None:
    """Reject inconsistent output before calculating metrics from it."""

    if plan.scenario_id != state.scenario_id or plan.graph_revision != state.graph_revision:
        raise ValueError("plan does not match its scenario graph snapshot")
    request_by_id = {request.request_id: request for request in state.requests}
    pair_by_ids = {
        (pair.request_id, pair.team_id): pair for pair in state.feasible_pairs
    }
    covered = {assignment.request_id for assignment in plan.assignments} | {
        item.request_id for item in plan.unserved_requests
    }
    if covered != set(request_by_id):
        raise ValueError("plan must account for every request")
    for assignment in plan.assignments:
        pair = pair_by_ids.get((assignment.request_id, assignment.team_id))
        if (
            pair is None
            or pair.route != assignment.route
            or pair.eta_seconds != assignment.eta_seconds
        ):
            raise ValueError("assignment must retain the shared feasible route and ETA")
        request = request_by_id[assignment.request_id]
        elapsed = (assignment.predicted_arrival_at - request.received_at).total_seconds()
        if not math.isclose(elapsed, assignment.predicted_response_seconds, abs_tol=1e-9):
            raise ValueError("predicted response must include pre-dispatch waiting")
    pending_loss = sum(
        request_by_id[item.request_id].dispatch_weight or 0
        for item in plan.unserved_requests
    )
    weighted_travel = sum(
        (request_by_id[item.request_id].dispatch_weight or 0) * item.eta_seconds
        for item in plan.assignments
    )
    objective = plan.objective_value
    if (
        objective.weighted_pending_loss != pending_loss
        or objective.weighted_travel_seconds != weighted_travel
    ):
        raise ValueError("plan objective P/Q does not match assignments")


def _request_rows(
    state: DispatchState,
    plan: DispatchPlan,
    *,
    seed: int,
    design: str,
    origin: str,
    horizon_seconds: int,
) -> list[dict[str, Any]]:
    assigned = {item.request_id: item for item in plan.assignments}
    unserved = {item.request_id: item for item in plan.unserved_requests}
    objective = plan.objective_value
    solver_status = plan.explanation_metadata.get("solver_status", "not_reported")
    rows: list[dict[str, Any]] = []
    for request in sorted(state.requests, key=lambda item: item.request_id):
        assignment = assigned.get(request.request_id)
        response_seconds = (
            assignment.predicted_response_seconds if assignment is not None else None
        )
        reached = response_seconds is not None and response_seconds <= horizon_seconds
        row: dict[str, Any] = {
            "generator_version": GENERATOR_VERSION,
            "seed": seed,
            "scenario_id": state.scenario_id,
            "scenario_design": design,
            "scenario_origin": origin,
            "strategy": plan.strategy_name,
            "graph_revision": state.graph_revision,
            "weight_policy_version": state.weight_policy_version,
            "request_id": request.request_id,
            "received_at_utc": request.received_at.isoformat(),
            "decision_time_utc": state.decision_time.isoformat(),
            "dispatch_weight": request.dispatch_weight,
            "evaluation_weight": request.evaluation_weight,
            "critical_cue": request.critical_cue,
            "routing_anchor_accepted": request.routing_anchor_accepted,
            "assigned_team_id": assignment.team_id if assignment is not None else None,
            "plan_assigned": assignment is not None,
            "reached_by_horizon": reached,
            "unserved_reasons": [reason.value for reason in unserved[request.request_id].reasons]
            if assignment is None else [],
            "eta_seconds": assignment.eta_seconds if assignment is not None else None,
            "predicted_arrival_at_utc": (
                assignment.predicted_arrival_at.isoformat() if assignment is not None else None
            ),
            "predicted_response_seconds": response_seconds,
            "route_distance_meters": (
                assignment.route.distance_meters if assignment is not None else None
            ),
            "route_edge_ids": (
                list(assignment.route.edge_ids) if assignment is not None else []
            ),
            "capped_response_seconds": (
                min(response_seconds, horizon_seconds)
                if response_seconds is not None else horizon_seconds
            ),
            "horizon_seconds": horizon_seconds,
            "plan_status": plan.status.value,
            "solver_status": solver_status,
            "objective_P": objective.weighted_pending_loss,
            "objective_Q_seconds": objective.weighted_travel_seconds,
            "objective_K_seconds": objective.dominance_constant,
            "objective_C": objective.scalar_cost,
        }
        rows.append(row)
    return rows


def _scenario_metrics(
    state: DispatchState,
    plan: DispatchPlan,
    rows: list[dict[str, Any]],
    *,
    design: str,
    origin: str,
    seed: int,
    solve_latency_ns: int,
) -> dict[str, Any]:
    served = [row for row in rows if row["reached_by_horizon"]]
    critical = [row for row in rows if row["critical_cue"]]
    critical_served = [row for row in served if row["critical_cue"]]
    served_eval_weight = sum(row["evaluation_weight"] for row in served)
    all_eval_weight = sum(row["evaluation_weight"] for row in rows)
    objective = plan.objective_value
    return {
        "seed": seed,
        "scenario_id": state.scenario_id,
        "scenario_design": design,
        "scenario_origin": origin,
        "strategy": plan.strategy_name,
        "graph_revision": state.graph_revision,
        "weight_policy_version": state.weight_policy_version,
        "request_count": len(rows),
        "plan_assigned_count": len(plan.assignments),
        "reached_by_horizon_count": len(served),
        "unreached_by_horizon_count": len(rows) - len(served),
        "coverage_fraction": len(served) / len(rows) if rows else None,
        "critical_cue_count": len(critical),
        "critical_cue_reached_count": len(critical_served),
        "critical_cue_unreached_fraction": (
            (len(critical) - len(critical_served)) / len(critical) if critical else None
        ),
        "mean_response_seconds_reached": (
            statistics.fmean(row["predicted_response_seconds"] for row in served)
            if served else None
        ),
        "mean_critical_response_seconds_reached": (
            statistics.fmean(row["predicted_response_seconds"] for row in critical_served)
            if critical_served else None
        ),
        "evaluation_weighted_mean_response_seconds_reached": (
            math.fsum(
                row["evaluation_weight"] * row["predicted_response_seconds"]
                for row in served
            ) / served_eval_weight if served_eval_weight else None
        ),
        "evaluation_weighted_capped_all_request_seconds": (
            math.fsum(
                row["evaluation_weight"] * row["capped_response_seconds"]
                for row in rows
            ) / all_eval_weight if all_eval_weight else None
        ),
        "planned_route_distance_meters": math.fsum(
            row["route_distance_meters"]
            for row in rows if row["route_distance_meters"] is not None
        ),
        "reached_route_distance_meters": math.fsum(
            row["route_distance_meters"]
            for row in served if row["route_distance_meters"] is not None
        ),
        "objective_P": objective.weighted_pending_loss,
        "objective_Q_seconds": objective.weighted_travel_seconds,
        "objective_K_seconds": objective.dominance_constant,
        "objective_C": objective.scalar_cost,
        "plan_status": plan.status.value,
        "solver_status": plan.explanation_metadata.get("solver_status", "not_reported"),
        "solve_latency_ns": solve_latency_ns,
    }


def run_comparison(
    *,
    seed: int = 412073,
    scenario_count: int = 8,
    horizon_seconds: int = HORIZON_SECONDS,
    output_prefix: Path = DEFAULT_OUTPUT_PREFIX,
) -> dict[str, Any]:
    """Run all registered strategies and persist measured raw CSV/JSON results."""

    if horizon_seconds < 1:
        raise ValueError("horizon_seconds must be positive")
    scenarios = generate_scenarios(seed=seed, count=scenario_count)
    names = list_strategies()
    if len(names) != 4 or len(set(names)) != 4:
        raise ValueError("comparison requires the four distinct registered strategies")
    request_rows: list[dict[str, Any]] = []
    scenario_metrics: list[dict[str, Any]] = []
    for scenario in scenarios:
        state = scenario.state
        original_state = state.model_dump_json()
        for name in names:
            started = perf_counter_ns()
            plan = get_strategy(name).solve(state)
            solve_latency_ns = perf_counter_ns() - started
            if state.model_dump_json() != original_state:
                raise ValueError("strategy mutated its shared dispatch state")
            if plan.strategy_name != name:
                raise ValueError("registry name and plan strategy name differ")
            _validate_plan(state, plan)
            rows = _request_rows(
                state,
                plan,
                seed=seed,
                design=scenario.design,
                origin=scenario.origin,
                horizon_seconds=horizon_seconds,
            )
            request_rows.extend(rows)
            scenario_metrics.append(
                _scenario_metrics(
                    state,
                    plan,
                    rows,
                    design=scenario.design,
                    origin=scenario.origin,
                    seed=seed,
                    solve_latency_ns=solve_latency_ns,
                )
            )
    report: dict[str, Any] = {
        "experiment": "dispatch_strategy_comparison_v1",
        "synthetic": True,
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "scenario_count": len(scenarios),
        "hand_constructed_control_count": sum(
            scenario.origin == "hand_constructed_control" for scenario in scenarios
        ),
        "seeded_generated_count": sum(
            scenario.origin == "seeded_generated" for scenario in scenarios
        ),
        "strategies": list(names),
        "weight_policy_version": WEIGHT_POLICY_VERSION,
        "horizon_seconds": horizon_seconds,
        "approval_assumption": "instant simulated approval at each scenario decision time",
        "simulation_scope": (
            "single wave; first team arrival at planned ETA; "
            "no later dispatch or replanning"
        ),
        "route_cost_config": ROUTING_CONFIG.model_dump(mode="json"),
        "scenario_inputs": [
            {
                "design": scenario.design,
                "origin": scenario.origin,
                "state": scenario.state.model_dump(mode="json"),
            }
            for scenario in scenarios
        ],
        "scenario_metrics": scenario_metrics,
        "request_metrics": request_rows,
    }
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = output_prefix.with_suffix(".json")
    csv_path = output_prefix.with_suffix(".csv")
    scenario_csv_path = output_prefix.with_suffix(".scenarios.csv")
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not request_rows:
        raise ValueError("at least one request row is required")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(request_rows[0]))
        writer.writeheader()
        for row in request_rows:
            csv_row = {
                key: json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value
                for key, value in row.items()
            }
            writer.writerow(csv_row)
    with scenario_csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(scenario_metrics[0]))
        writer.writeheader()
        writer.writerows(scenario_metrics)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=412073)
    parser.add_argument("--scenarios", type=int, default=8)
    parser.add_argument("--horizon-seconds", type=int, default=HORIZON_SECONDS)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args(argv)
    report = run_comparison(
        seed=args.seed,
        scenario_count=args.scenarios,
        horizon_seconds=args.horizon_seconds,
        output_prefix=args.output_prefix,
    )
    print(
        json.dumps(
            {
                "scenario_count": report["scenario_count"],
                "strategies": report["strategies"],
                "request_rows": len(report["request_metrics"]),
                "scenario_rows": len(report["scenario_metrics"]),
                "json": str(args.output_prefix.with_suffix(".json")),
                "csv": str(args.output_prefix.with_suffix(".csv")),
                "scenario_csv": str(args.output_prefix.with_suffix(".scenarios.csv")),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
