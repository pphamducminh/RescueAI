"""Record raw Dijkstra route, road-block, and replan latency on seeded grids.

The grid and chosen blocked road are deterministic for a seed and side length.
Wall-clock timings depend on the machine and its current load. Graph construction,
warmups, validation, and road restoration are outside the measured operations.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import platform
import statistics
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter_ns
from typing import Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

from backend.routing import RoadStatus, RoutingCostConfig
from evaluation.road_graph_benchmark import BASE_TIMESTAMP, DEFAULT_SIDES, _node_id, build_grid

DEFAULT_OUTPUT_DIR = Path("evaluation/results/routing_benchmark")
DEFAULT_SEED = 412073
DEFAULT_REPEATS = 30
DEFAULT_WARMUPS = 3
TRIALS_FILENAME = "routing_trials.csv"
SUMMARY_FILENAME = "routing_summary.json"


class RoutingTrial(BaseModel):
    """One measured iteration; all required values must be present and finite."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    grid_side: StrictInt = Field(ge=2)
    seed: StrictInt
    trial_index: StrictInt = Field(ge=0)
    nodes: StrictInt = Field(ge=4)
    directed_edges: StrictInt = Field(ge=1)
    origin_node_id: StrictStr = Field(min_length=1)
    destination_node_id: StrictStr = Field(min_length=1)
    blocked_edge_id: StrictStr = Field(min_length=1)
    baseline_edge_ids: tuple[StrictStr, ...] = Field(min_length=1)
    replanned_edge_ids: tuple[StrictStr, ...] = Field(min_length=1)
    baseline_graph_revision: StrictInt = Field(ge=0)
    blocked_graph_revision: StrictInt = Field(ge=0)
    baseline_travel_time_seconds: FiniteFloat = Field(ge=0)
    replanned_travel_time_seconds: FiniteFloat = Field(ge=0)
    baseline_effective_cost_seconds: FiniteFloat = Field(ge=0)
    replanned_effective_cost_seconds: FiniteFloat = Field(ge=0)
    route_runtime_ns: StrictInt = Field(ge=0)
    block_runtime_ns: StrictInt = Field(ge=0)
    replan_runtime_ns: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def check_replan(self) -> Self:
        if self.blocked_edge_id not in self.baseline_edge_ids:
            raise ValueError("blocked road was absent from the baseline route")
        if self.blocked_edge_id in self.replanned_edge_ids:
            raise ValueError("replanned route used the blocked road")
        if self.blocked_graph_revision <= self.baseline_graph_revision:
            raise ValueError("road block did not advance the graph revision")
        if self.nodes != self.grid_side**2:
            raise ValueError("node count does not match grid side")
        if self.directed_edges != 4 * self.grid_side * (self.grid_side - 1):
            raise ValueError("directed edge count does not match grid side")
        return self


CSV_COLUMNS = tuple(RoutingTrial.model_fields)


def _validate_trials(
    trials: list[RoutingTrial], *, sides: tuple[int, ...], seed: int, repeats: int
) -> None:
    """Reject missing, duplicate, or inconsistent measured trials before writing."""

    expected_keys = {(side, trial) for side in sides for trial in range(repeats)}
    actual_keys = [(row.grid_side, row.trial_index) for row in trials]
    if len(actual_keys) != len(expected_keys) or set(actual_keys) != expected_keys:
        raise ValueError("measured trials are missing, duplicated, or unexpected")
    if any(row.seed != seed for row in trials):
        raise ValueError("measured trials contain an unexpected seed")


def _latency_summary(values: list[int]) -> dict[str, int | float]:
    if not values:
        raise ValueError("latency summary requires measured samples")
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "min_ns": ordered[0],
        "median_ns": statistics.median(ordered),
        "mean_ns": statistics.fmean(ordered),
        "p95_ns": ordered[math.ceil(0.95 * len(ordered)) - 1],
        "max_ns": ordered[-1],
    }


def _measure_grid(
    side: int,
    *,
    seed: int,
    repeats: int,
    warmups: int,
    config: RoutingCostConfig,
) -> list[RoutingTrial]:
    graph = build_grid(side, seed, config)
    origin = _node_id(0, 0)
    destination = _node_id(side - 1, side - 1)
    rows: list[RoutingTrial] = []

    for iteration in range(warmups + repeats):
        started = perf_counter_ns()
        baseline = graph.shortest_safe_route(origin, destination)
        route_runtime_ns = perf_counter_ns() - started
        if baseline is None or not baseline.edge_ids:
            raise RuntimeError(f"No baseline route for {side} x {side} grid")

        blocked_edge_id = baseline.edge_ids[len(baseline.edge_ids) // 2]
        block_time = BASE_TIMESTAMP + timedelta(seconds=2 * iteration + 1)
        restore_time = BASE_TIMESTAMP + timedelta(seconds=2 * iteration + 2)
        started = perf_counter_ns()
        graph.block_road(blocked_edge_id, updated_at=block_time)
        block_runtime_ns = perf_counter_ns() - started
        if graph.get_road(blocked_edge_id).status is not RoadStatus.BLOCKED:
            raise RuntimeError("Road block was not applied")

        started = perf_counter_ns()
        replanned = graph.recompute_route(baseline)
        replan_runtime_ns = perf_counter_ns() - started
        if replanned is None:
            raise RuntimeError("No route after blocking a baseline road")
        if replanned.node_ids[0] != origin or replanned.node_ids[-1] != destination:
            raise RuntimeError("Replanned route changed its endpoints")
        if replanned.graph_revision != graph.revision:
            raise RuntimeError("Replanned route does not use the current graph revision")

        if iteration >= warmups:
            rows.append(
                RoutingTrial(
                    grid_side=side,
                    seed=seed,
                    trial_index=iteration - warmups,
                    nodes=side * side,
                    directed_edges=4 * side * (side - 1),
                    origin_node_id=origin,
                    destination_node_id=destination,
                    blocked_edge_id=blocked_edge_id,
                    baseline_edge_ids=baseline.edge_ids,
                    replanned_edge_ids=replanned.edge_ids,
                    baseline_graph_revision=baseline.graph_revision,
                    blocked_graph_revision=graph.revision,
                    baseline_travel_time_seconds=baseline.travel_time_seconds,
                    replanned_travel_time_seconds=replanned.travel_time_seconds,
                    baseline_effective_cost_seconds=baseline.effective_cost_seconds,
                    replanned_effective_cost_seconds=replanned.effective_cost_seconds,
                    route_runtime_ns=route_runtime_ns,
                    block_runtime_ns=block_runtime_ns,
                    replan_runtime_ns=replan_runtime_ns,
                )
            )

        graph.unblock_road(blocked_edge_id, updated_at=restore_time)

    return rows


def _csv_row(trial: RoutingTrial) -> dict[str, str | int | float]:
    values = trial.model_dump(mode="python")
    values["baseline_edge_ids"] = json.dumps(values["baseline_edge_ids"])
    values["replanned_edge_ids"] = json.dumps(values["replanned_edge_ids"])
    if set(values) != set(CSV_COLUMNS) or any(
        value is None or value == "" for value in values.values()
    ):
        raise ValueError("trial CSV row contains missing fields")
    return values


def _validate_manifest(value: Any, *, location: str = "report") -> None:
    """Refuse missing, blank, or non-finite values in the JSON manifest."""

    if value is None:
        raise ValueError(f"{location} is missing")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{location} is non-finite")
    if isinstance(value, str) and not value.strip():
        raise ValueError(f"{location} is blank")
    if isinstance(value, dict):
        for key, child in value.items():
            _validate_manifest(child, location=f"{location}.{key}")
    if isinstance(value, list):
        for index, child in enumerate(value):
            _validate_manifest(child, location=f"{location}[{index}]")


def run_benchmark(
    *,
    sides: tuple[int, ...] = DEFAULT_SIDES,
    seed: int = DEFAULT_SEED,
    repeats: int = DEFAULT_REPEATS,
    warmups: int = DEFAULT_WARMUPS,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> dict[str, Any]:
    """Run seeded workloads, save every trial as CSV, then save a JSON summary."""

    if not sides or any(side < 2 for side in sides) or len(sides) != len(set(sides)):
        raise ValueError("Provide at least one unique grid side of two or more")
    if repeats < 1 or warmups < 0:
        raise ValueError("Repeats must be positive and warmups cannot be negative")

    config = RoutingCostConfig(
        risk_penalty_seconds_per_unit=20.0,
        degraded_time_multiplier=1.5,
    )
    trials = [
        trial
        for side in sides
        for trial in _measure_grid(
            side, seed=seed, repeats=repeats, warmups=warmups, config=config
        )
    ]
    _validate_trials(trials, sides=sides, seed=seed, repeats=repeats)

    grouped: dict[int, list[RoutingTrial]] = {side: [] for side in sides}
    for trial in trials:
        grouped[trial.grid_side].append(trial)
    summaries = [
        {
            "grid_side": side,
            "nodes": side * side,
            "directed_edges": 4 * side * (side - 1),
            "trial_count": len(grouped[side]),
            "route_runtime_ns": _latency_summary(
                [trial.route_runtime_ns for trial in grouped[side]]
            ),
            "block_runtime_ns": _latency_summary(
                [trial.block_runtime_ns for trial in grouped[side]]
            ),
            "replan_runtime_ns": _latency_summary(
                [trial.replan_runtime_ns for trial in grouped[side]]
            ),
        }
        for side in sides
    ]

    csv_path = output_dir / TRIALS_FILENAME
    summary_path = output_dir / SUMMARY_FILENAME
    report: dict[str, Any] = {
        "benchmark": "road_graph_dijkstra_raw_trials_v1",
        "synthetic": True,
        "measured_at_utc": datetime.now(UTC).isoformat(),
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine() or "unknown",
            "processor": platform.processor() or "unknown",
            "python_version": platform.python_version(),
        },
        "configuration": {
            "algorithm": "Dijkstra",
            "directed": True,
            "grid_sides": list(sides),
            "seed": seed,
            "graph_seed_rule": "seed + grid_side",
            "repeats_per_side": repeats,
            "warmups_per_side": warmups,
            "routing_cost": config.model_dump(mode="json"),
            "edge_status_before_each_trial": RoadStatus.OPEN.value,
            "measurement_unit": "nanoseconds",
            "timer_scope": {
                "route": "shortest_safe_route only",
                "block": "block_road only",
                "replan": "recompute_route only after a baseline road is blocked",
            },
            "excluded_from_timers": [
                "grid construction",
                "warmups",
                "blocked-road selection and validation",
                "restoration after each trial",
                "CSV and JSON serialization",
            ],
            "summary_statistics": "descriptive per side; p95 uses the nearest-rank method",
            "timing_reproducibility": (
                "workload repeats for a seed; wall time varies by machine/load"
            ),
        },
        "raw_results": {
            "csv_path": str(csv_path),
            "row_count": len(trials),
            "columns": list(CSV_COLUMNS),
        },
        "results": summaries,
    }
    # Refuse non-finite summary metrics before creating any output files.
    _validate_manifest(report)
    serialized_report = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    output_dir.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for trial in trials:
            writer.writerow(_csv_row(trial))
    summary_path.write_text(serialized_report, encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sides", nargs="+", type=int, default=list(DEFAULT_SIDES))
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--warmups", type=int, default=DEFAULT_WARMUPS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    report = run_benchmark(
        sides=tuple(args.sides),
        seed=args.seed,
        repeats=args.repeats,
        warmups=args.warmups,
        output_dir=args.output_dir,
    )
    print(
        json.dumps(
            {
                "raw_csv": report["raw_results"]["csv_path"],
                "summary_json": str(args.output_dir / SUMMARY_FILENAME),
                "trial_count": report["raw_results"]["row_count"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
