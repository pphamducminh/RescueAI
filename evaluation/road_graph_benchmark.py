"""Measure Dijkstra routing and replanning on reproducible directed grids.

Graph construction, warmups, and restoring a blocked edge are excluded from
the route and replan timings. Blocking is measured separately. The graph input
is deterministic for a given seed; wall-clock latency is machine dependent.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import random
import statistics
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from backend.routing import DynamicRoadGraph, Road, RoadStatus, RoutingCostConfig

BASE_TIMESTAMP = datetime(2026, 1, 1, tzinfo=UTC)
DEFAULT_OUTPUT = Path("evaluation/results/road_graph_latency.json")
DEFAULT_SIDES = (5, 10, 20, 30)


def _node_id(row: int, column: int) -> str:
    return f"n_{row:03d}_{column:03d}"


def build_grid(side: int, seed: int, config: RoutingCostConfig) -> DynamicRoadGraph:
    """Build a directed square grid with reverse roads and seeded risk values."""
    if side < 2:
        raise ValueError("Grid side must be at least 2")

    graph = DynamicRoadGraph(config)
    random_source = random.Random(seed + side)
    for row in range(side):
        for column in range(side):
            for next_row, next_column in ((row + 1, column), (row, column + 1)):
                if next_row >= side or next_column >= side:
                    continue
                for source_row, source_column, target_row, target_column in (
                    (row, column, next_row, next_column),
                    (next_row, next_column, row, column),
                ):
                    horizontal = source_row == target_row
                    graph.add_road(
                        Road(
                            edge_id=(
                                f"e_{source_row:03d}_{source_column:03d}"
                                f"__{target_row:03d}_{target_column:03d}"
                            ),
                            source_node_id=_node_id(source_row, source_column),
                            target_node_id=_node_id(target_row, target_column),
                            distance_meters=100.0 if horizontal else 120.0,
                            base_travel_time_seconds=12.0 if horizontal else 16.0,
                            risk_score=random_source.random(),
                            status=RoadStatus.OPEN,
                            last_updated=BASE_TIMESTAMP,
                        )
                    )
    return graph


def _latency_summary(samples_ns: list[int]) -> dict[str, int | float]:
    ordered = sorted(samples_ns)
    return {
        "min_ns": ordered[0],
        "median_ns": statistics.median(ordered),
        "mean_ns": statistics.fmean(ordered),
        "p95_ns": ordered[math.ceil(0.95 * len(ordered)) - 1],
        "max_ns": ordered[-1],
    }


def benchmark_grid(
    side: int,
    *,
    seed: int,
    repeats: int,
    warmups: int,
    config: RoutingCostConfig,
) -> dict[str, Any]:
    """Measure a route and its recomputation after one route edge is blocked."""
    if repeats < 1 or warmups < 0:
        raise ValueError("Repeats must be positive and warmups cannot be negative")

    graph = build_grid(side, seed, config)
    origin = _node_id(0, 0)
    destination = _node_id(side - 1, side - 1)
    route_samples: list[int] = []
    block_samples: list[int] = []
    replan_samples: list[int] = []
    edge_count = 4 * side * (side - 1)
    baseline_edge_count = 0
    replanned_edge_count = 0

    for trial in range(warmups + repeats):
        started = perf_counter_ns()
        baseline = graph.shortest_safe_route(origin, destination)
        route_elapsed = perf_counter_ns() - started
        if baseline is None or not baseline.edge_ids:
            raise RuntimeError(f"No baseline path in {side} x {side} grid")

        blocked_edge_id = baseline.edge_ids[len(baseline.edge_ids) // 2]
        block_time = BASE_TIMESTAMP + timedelta(seconds=2 * trial + 1)
        restore_time = BASE_TIMESTAMP + timedelta(seconds=2 * trial + 2)
        started = perf_counter_ns()
        graph.block_road(blocked_edge_id, updated_at=block_time)
        block_elapsed = perf_counter_ns() - started

        started = perf_counter_ns()
        replanned = graph.recompute_route(baseline)
        replan_elapsed = perf_counter_ns() - started
        if replanned is None or blocked_edge_id in replanned.edge_ids:
            raise RuntimeError("Replanning failed to avoid the newly blocked road")

        graph.unblock_road(blocked_edge_id, updated_at=restore_time)
        if trial >= warmups:
            route_samples.append(route_elapsed)
            block_samples.append(block_elapsed)
            replan_samples.append(replan_elapsed)
            baseline_edge_count = len(baseline.edge_ids)
            replanned_edge_count = len(replanned.edge_ids)

    return {
        "grid_side": side,
        "nodes": side * side,
        "directed_edges": edge_count,
        "origin": origin,
        "destination": destination,
        "baseline_route_edges": baseline_edge_count,
        "replanned_route_edges": replanned_edge_count,
        "route_latency": _latency_summary(route_samples),
        "block_update_latency": _latency_summary(block_samples),
        "replanning_latency": _latency_summary(replan_samples),
    }


def run_benchmark(
    *,
    sides: tuple[int, ...] = DEFAULT_SIDES,
    seed: int = 412073,
    repeats: int = 100,
    warmups: int = 10,
    output: Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    """Run the workload and write the measured results as JSON."""
    if not sides or len(set(sides)) != len(sides):
        raise ValueError("At least one unique grid side is required")

    config = RoutingCostConfig(
        risk_penalty_seconds_per_unit=20.0,
        degraded_time_multiplier=1.5,
    )
    results = [
        benchmark_grid(side, seed=seed, repeats=repeats, warmups=warmups, config=config)
        for side in sides
    ]
    report: dict[str, Any] = {
        "benchmark": "road_graph_dijkstra_latency_v1",
        "measured_at_utc": datetime.now(UTC).isoformat(),
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python_version": platform.python_version(),
        },
        "workload": {
            "algorithm": "Dijkstra",
            "directed": True,
            "grid_sides": list(sides),
            "seed": seed,
            "repeats": repeats,
            "warmups": warmups,
            "risk_penalty_seconds_per_unit": config.risk_penalty_seconds_per_unit,
            "degraded_time_multiplier": config.degraded_time_multiplier,
            "edge_status": RoadStatus.OPEN.value,
            "measurement_unit": "nanoseconds",
            "route_timer": "shortest_safe_route only",
            "replan_timer": "recompute_route only, after blocking one baseline route edge",
            "block_timer": "block_road only",
            "excluded_from_timers": [
                "graph construction",
                "warmups",
                "restore of blocked edge after each trial",
            ],
        },
        "results": results,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sides", nargs="+", type=int, default=list(DEFAULT_SIDES))
    parser.add_argument("--seed", type=int, default=412073)
    parser.add_argument("--repeats", type=int, default=100)
    parser.add_argument("--warmups", type=int, default=10)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args(argv)
    report = run_benchmark(
        sides=tuple(arguments.sides),
        seed=arguments.seed,
        repeats=arguments.repeats,
        warmups=arguments.warmups,
        output=arguments.output,
    )
    print(json.dumps({"output": str(arguments.output), "results": report["results"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
