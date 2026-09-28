"""Run paired, offline, one-wave dispatch benchmarks on seeded synthetic cases.

Each generated scenario is built once per seed and passed unchanged to every
selected strategy. The benchmark records every request and strategy run before
producing descriptive summaries. It does not simulate later dispatch waves.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter_ns
from types import MappingProxyType
from typing import Any

from backend.dispatch import DispatchState, get_strategy, list_strategies

from .dispatch_comparison import (
    GENERATOR_VERSION,
    ROUTING_CONFIG,
    _request_rows,
    _scenario_metrics,
    _validate_plan,
    generate_generated_scenarios,
)

DEFAULT_OUTPUT_DIR = Path("evaluation/results/benchmark")
DEFAULT_HORIZON_SECONDS = 7200
STRATEGY_ALIASES = MappingProxyType(
    {
        "fcfs": "fcfs",
        "nearest": "nearest_team",
        "nearest_team": "nearest_team",
        "greedy": "priority_aware_greedy",
        "priority_aware_greedy": "priority_aware_greedy",
        "rescueai": "rescueai_optimizer",
        "rescueai_optimizer": "rescueai_optimizer",
    }
)
VARIANTS = ("baseline", "uniform_weights")


@dataclass(frozen=True)
class BenchmarkConfig:
    """Frozen run settings; ``scenarios_per_seed`` excludes diagnostic controls."""

    scenarios_per_seed: int
    seeds: tuple[int, ...]
    strategies: tuple[str, ...]
    variants: tuple[str, ...] = ("baseline",)
    horizon_seconds: int = DEFAULT_HORIZON_SECONDS
    output_dir: Path = DEFAULT_OUTPUT_DIR

    def __post_init__(self) -> None:
        if self.scenarios_per_seed < 1 or self.horizon_seconds < 1:
            raise ValueError("scenarios_per_seed and horizon_seconds must be positive")
        if not self.seeds or len(self.seeds) != len(set(self.seeds)):
            raise ValueError("provide at least one distinct seed")
        if not self.variants or len(self.variants) != len(set(self.variants)):
            raise ValueError("provide at least one distinct variant")
        if any(variant not in VARIANTS for variant in self.variants):
            raise ValueError(f"variants must be chosen from {VARIANTS}")
        if not self.strategies:
            raise ValueError("provide at least one strategy")
        canonical: list[str] = []
        for name in self.strategies:
            try:
                canonical.append(STRATEGY_ALIASES[name])
            except KeyError as exc:
                raise ValueError(f"unknown strategy {name!r}") from exc
        if len(canonical) != len(set(canonical)) or not set(canonical).issubset(
            list_strategies()
        ):
            raise ValueError("strategies must be distinct registered policies")
        object.__setattr__(self, "strategies", tuple(canonical))


def apply_variant(state: DispatchState, variant: str) -> DispatchState:
    """Change approved dispatch weights only; retain review and evaluation facts."""

    if variant == "baseline":
        return state
    if variant != "uniform_weights":
        raise ValueError(f"unknown benchmark variant {variant!r}")
    data = state.model_dump(mode="python")
    data["requests"] = [
        {
            **request.model_dump(mode="python"),
            "dispatch_weight": 1 if request.dispatch_weight is not None else None,
        }
        for request in state.requests
    ]
    data["weight_policy_version"] = "synthetic_uniform_dispatch_weight_v1"
    return DispatchState.model_validate(data)


def _snapshot_hash(state: DispatchState) -> str:
    encoded = json.dumps(
        state.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _code_hash() -> str:
    root = Path(__file__).resolve().parents[1]
    relative_paths = (
        "backend/dispatch/models.py",
        "backend/dispatch/state.py",
        "backend/dispatch/strategies.py",
        "backend/routing/graph.py",
        "evaluation/dispatch_comparison.py",
        "evaluation/run.py",
    )
    digest = hashlib.sha256()
    for relative_path in relative_paths:
        digest.update(relative_path.encode("utf-8"))
        digest.update((root / relative_path).read_bytes())
    return digest.hexdigest()


def _assert_finite(value: Any, context: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"non-finite number at {context}")
    if isinstance(value, dict):
        for key, child in value.items():
            _assert_finite(child, f"{context}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_finite(child, f"{context}[{index}]")


def _validate_request_rows(rows: list[dict[str, Any]], horizon_seconds: int) -> None:
    for row in rows:
        _assert_finite(row, f"request {row.get('request_id')}")
        if not row.get("request_id") or not row.get("scenario_hash"):
            raise ValueError("request row is missing an ID or scenario hash")
        if row["plan_assigned"]:
            if any(
                row[key] is None
                for key in (
                    "assigned_team_id",
                    "eta_seconds",
                    "predicted_arrival_at_utc",
                    "predicted_response_seconds",
                    "route_distance_meters",
                )
            ) or row["unserved_reasons"]:
                raise ValueError("assigned request has missing route/arrival data")
        elif (
            row["assigned_team_id"] is not None
            or row["predicted_response_seconds"] is not None
            or row["predicted_arrival_at_utc"] is not None
            or not row["unserved_reasons"]
        ):
            raise ValueError("unassigned request has an arrival or no pending reason")
        response = row["predicted_response_seconds"]
        if row["reached_by_horizon"] != (response is not None and response <= horizon_seconds):
            raise ValueError("reached flag disagrees with response and horizon")
        expected_cap = min(response, horizon_seconds) if response is not None else horizon_seconds
        if row["capped_response_seconds"] != expected_cap:
            raise ValueError("capped response disagrees with response and horizon")


def _validate_run_rows(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        _assert_finite(row, f"run {row.get('scenario_id')}:{row.get('strategy')}")
        if not row.get("scenario_hash") or not row.get("strategy"):
            raise ValueError("run row is missing a strategy or scenario hash")
        total = row["request_count"]
        reached = row["reached_by_horizon_count"]
        critical = row["critical_cue_count"]
        critical_reached = row["critical_cue_reached_count"]
        if total < 1 or reached + row["unreached_by_horizon_count"] != total:
            raise ValueError("run request/reached counts are inconsistent")
        if not 0 <= critical_reached <= critical <= total:
            raise ValueError("run critical counts are inconsistent")
        if not math.isclose(row["coverage_fraction"], reached / total, abs_tol=1e-12):
            raise ValueError("run coverage is inconsistent")
        if (row["mean_response_seconds_reached"] is None) != (reached == 0):
            raise ValueError("reached-only mean must be NA exactly when no one is reached")
        if (row["mean_critical_response_seconds_reached"] is None) != (
            critical_reached == 0
        ):
            raise ValueError("critical reached-only mean has wrong missingness")
        if (row["critical_capped_all_request_seconds"] is None) != (critical == 0):
            raise ValueError("critical capped mean has wrong missingness")
        if row["evaluation_weighted_capped_all_request_seconds"] is None:
            raise ValueError("all-request capped metric must be present")
        if row["solve_latency_ns"] is None or row["solve_latency_ns"] < 0:
            raise ValueError("algorithm runtime must be present and nonnegative")


def _extra_metrics(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    caps = sorted(float(row["capped_response_seconds"]) for row in rows)
    critical_caps = [
        float(row["capped_response_seconds"]) for row in rows if row["critical_cue"]
    ]
    return {
        "critical_capped_all_request_seconds": (
            statistics.fmean(critical_caps) if critical_caps else None
        ),
        "p90_capped_all_request_seconds": caps[math.ceil(0.9 * len(caps)) - 1],
    }


def _failure_request_rows(
    state: DispatchState,
    *,
    seed: int,
    design: str,
    origin: str,
    strategy: str,
    horizon_seconds: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for request in sorted(state.requests, key=lambda item: item.request_id):
        rows.append(
            {
                "generator_version": GENERATOR_VERSION,
                "seed": seed,
                "scenario_id": state.scenario_id,
                "scenario_design": design,
                "scenario_origin": origin,
                "strategy": strategy,
                "graph_revision": state.graph_revision,
                "weight_policy_version": state.weight_policy_version,
                "request_id": request.request_id,
                "received_at_utc": request.received_at.isoformat(),
                "decision_time_utc": state.decision_time.isoformat(),
                "dispatch_weight": request.dispatch_weight,
                "evaluation_weight": request.evaluation_weight,
                "critical_cue": request.critical_cue,
                "routing_anchor_accepted": request.routing_anchor_accepted,
                "assigned_team_id": None,
                "plan_assigned": False,
                "reached_by_horizon": False,
                "unserved_reasons": ["solver_failure"],
                "eta_seconds": None,
                "predicted_arrival_at_utc": None,
                "predicted_response_seconds": None,
                "route_distance_meters": None,
                "route_edge_ids": [],
                "capped_response_seconds": horizon_seconds,
                "horizon_seconds": horizon_seconds,
                "plan_status": None,
                "solver_status": "ERROR",
                "objective_P": None,
                "objective_Q_seconds": None,
                "objective_K_seconds": None,
                "objective_C": None,
            }
        )
    return rows


def _failure_metrics(
    state: DispatchState,
    rows: list[dict[str, Any]],
    *,
    design: str,
    origin: str,
    seed: int,
    solve_latency_ns: int,
    horizon_seconds: int,
) -> dict[str, Any]:
    critical_count = sum(request.critical_cue for request in state.requests)
    return {
        "seed": seed,
        "scenario_id": state.scenario_id,
        "scenario_design": design,
        "scenario_origin": origin,
        "strategy": rows[0]["strategy"],
        "graph_revision": state.graph_revision,
        "weight_policy_version": state.weight_policy_version,
        "request_count": len(rows),
        "plan_assigned_count": 0,
        "reached_by_horizon_count": 0,
        "unreached_by_horizon_count": len(rows),
        "coverage_fraction": 0.0,
        "critical_cue_count": critical_count,
        "critical_cue_reached_count": 0,
        "critical_cue_unreached_fraction": 1.0 if critical_count else None,
        "mean_response_seconds_reached": None,
        "mean_critical_response_seconds_reached": None,
        "evaluation_weighted_mean_response_seconds_reached": None,
        "evaluation_weighted_capped_all_request_seconds": float(horizon_seconds),
        "planned_route_distance_meters": 0.0,
        "reached_route_distance_meters": 0.0,
        "objective_P": None,
        "objective_Q_seconds": None,
        "objective_K_seconds": None,
        "objective_C": None,
        "plan_status": None,
        "solver_status": "ERROR",
        "solve_latency_ns": solve_latency_ns,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"no raw rows for {path}")
    fields = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            if set(row) != set(fields):
                raise ValueError(f"inconsistent raw CSV fields in {path}")
            writer.writerow(
                {
                    key: json.dumps(value, ensure_ascii=False, allow_nan=False)
                    if isinstance(value, list)
                    else value
                    for key, value in row.items()
                }
            )


def run_benchmark(config: BenchmarkConfig) -> dict[str, Any]:
    """Persist all paired raw records, validate them, then make a summary."""

    scenarios: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    for seed in config.seeds:
        for generated in generate_generated_scenarios(seed=seed, count=config.scenarios_per_seed):
            source_hash = _snapshot_hash(generated.state)
            for variant in config.variants:
                state = apply_variant(generated.state, variant)
                scenario_hash = _snapshot_hash(state)
                scenarios.append(
                    {
                        "seed": seed,
                        "scenario_id": state.scenario_id,
                        "variant": variant,
                        "scenario_origin": generated.origin,
                        "scenario_design": generated.design,
                        "source_scenario_hash": source_hash,
                        "scenario_hash": scenario_hash,
                        "state": state.model_dump(mode="json"),
                    }
                )
                original_state = state.model_dump_json()
                for strategy_name in config.strategies:
                    strategy = get_strategy(strategy_name)
                    started = perf_counter_ns()
                    try:
                        plan = strategy.solve(state)
                    except Exception as exc:
                        elapsed = perf_counter_ns() - started
                        error: Exception | None = exc
                        plan = None
                    else:
                        elapsed = perf_counter_ns() - started
                        error = None
                    if state.model_dump_json() != original_state:
                        raise ValueError("strategy mutated the shared scenario state")
                    if plan is not None:
                        try:
                            if plan.strategy_name != strategy_name:
                                raise ValueError("strategy returned the wrong plan name")
                            _validate_plan(state, plan)
                        except Exception as exc:
                            error = exc
                            plan = None
                    if plan is None:
                        assert error is not None
                        request_rows = _failure_request_rows(
                            state,
                            seed=seed,
                            design=generated.design,
                            origin=generated.origin,
                            strategy=strategy_name,
                            horizon_seconds=config.horizon_seconds,
                        )
                        metric = _failure_metrics(
                            state,
                            request_rows,
                            design=generated.design,
                            origin=generated.origin,
                            seed=seed,
                            solve_latency_ns=elapsed,
                            horizon_seconds=config.horizon_seconds,
                        )
                        run_status = "solver_error"
                        error_type = type(error).__name__
                        error_message = str(error)
                    else:
                        request_rows = _request_rows(
                            state,
                            plan,
                            seed=seed,
                            design=generated.design,
                            origin=generated.origin,
                            horizon_seconds=config.horizon_seconds,
                        )
                        metric = _scenario_metrics(
                            state,
                            plan,
                            request_rows,
                            design=generated.design,
                            origin=generated.origin,
                            seed=seed,
                            solve_latency_ns=elapsed,
                        )
                        run_status = "ok"
                        error_type = ""
                        error_message = ""
                    extra = _extra_metrics(request_rows)
                    metric.update(extra)
                    metric.update(
                        variant=variant,
                        source_scenario_hash=source_hash,
                        scenario_hash=scenario_hash,
                        run_status=run_status,
                        error_type=error_type,
                        error_message=error_message,
                    )
                    for row in request_rows:
                        row.update(
                            variant=variant,
                            source_scenario_hash=source_hash,
                            scenario_hash=scenario_hash,
                            run_status=run_status,
                            error_type=error_type,
                            error_message=error_message,
                        )
                    runs.append(metric)
                    requests.extend(request_rows)
    _validate_run_rows(runs)
    _validate_request_rows(requests, config.horizon_seconds)
    _assert_finite(scenarios, "scenarios")
    manifest: dict[str, Any] = {
        "benchmark": "synthetic_one_wave_dispatch_v1",
        "synthetic": True,
        "config": {
            "scenarios_per_seed": config.scenarios_per_seed,
            "seeds": list(config.seeds),
            "strategies": list(config.strategies),
            "variants": list(config.variants),
            "horizon_seconds": config.horizon_seconds,
        },
        "generated_scenario_count": len(config.seeds) * config.scenarios_per_seed,
        "scenario_variant_count": len(scenarios),
        "strategy_run_count": len(runs),
        "request_row_count": len(requests),
        "generator_version": GENERATOR_VERSION,
        "route_cost_config": ROUTING_CONFIG.model_dump(mode="json"),
        "algorithm_runtime_scope": "strategy.solve only; route building excluded",
        "simulation_scope": (
            "one wave; immediate simulated approval; first arrival at planned route time; "
            "no later dispatch or replanning"
        ),
        "code_sha256": _code_hash(),
        "environment": {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "files": ["manifest.json", "scenarios.jsonl", "runs.csv", "requests.csv", "summary.json"],
    }
    _assert_finite(manifest, "manifest")
    config.output_dir.mkdir(parents=True, exist_ok=True)
    (config.output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (config.output_dir / "scenarios.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True, allow_nan=False) + "\n" for row in scenarios),
        encoding="utf-8",
    )
    _write_csv(config.output_dir / "runs.csv", runs)
    _write_csv(config.output_dir / "requests.csv", requests)
    from .aggregate_metrics import aggregate_metrics

    return aggregate_metrics(config.output_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=int, default=100, help="generated cases per seed")
    parser.add_argument("--seeds", type=int, nargs="+", default=[1])
    parser.add_argument(
        "--strategies", nargs="+", default=["fcfs", "nearest", "greedy", "rescueai"]
    )
    parser.add_argument("--horizon-seconds", type=int, default=DEFAULT_HORIZON_SECONDS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    try:
        config = BenchmarkConfig(
            scenarios_per_seed=args.scenarios,
            seeds=tuple(args.seeds),
            strategies=tuple(args.strategies),
            horizon_seconds=args.horizon_seconds,
            output_dir=args.output_dir,
        )
    except ValueError as exc:
        parser.error(str(exc))
    summary = run_benchmark(config)
    print(
        json.dumps(
            {
                "output_dir": str(config.output_dir),
                "scenario_variant_count": summary["scenario_variant_count"],
                "strategy_run_count": summary["strategy_run_count"],
                "failure_count": summary["failure_count"],
                "raw_files": ["scenarios.jsonl", "runs.csv", "requests.csv"],
                "summary": "summary.json",
            },
            indent=2,
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
