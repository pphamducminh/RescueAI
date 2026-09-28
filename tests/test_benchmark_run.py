"""Contract tests for the seeded, raw-output dispatch benchmark runner."""

from __future__ import annotations

import csv
import json
import math
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

import evaluation.run as benchmark_runner
from backend.dispatch import get_strategy
from evaluation.dispatch_comparison import generate_scenarios
from evaluation.run import BenchmarkConfig, apply_variant, run_benchmark

STRATEGIES = ("fcfs", "nearest_team", "priority_aware_greedy", "rescueai_optimizer")


def _json(path: Path) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"nonfinite JSON number {value}")

    loaded: dict[str, Any] = json.loads(
        path.read_text(encoding="utf-8"), parse_constant=reject_constant
    )
    return loaded


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as source:
        return list(csv.DictReader(source))


def _config(output_dir: Path, **overrides: Any) -> BenchmarkConfig:
    fields: dict[str, Any] = {
        "scenarios_per_seed": 3,
        "seeds": (7, 11),
        "strategies": STRATEGIES,
        "output_dir": output_dir,
    }
    fields.update(overrides)
    return BenchmarkConfig(**fields)


def test_runner_persists_shared_scenarios_and_every_raw_strategy_result(tmp_path: Path) -> None:
    output = tmp_path / "benchmark"
    run_benchmark(_config(output))

    manifest = _json(output / "manifest.json")
    scenarios = _jsonl(output / "scenarios.jsonl")
    runs = _csv(output / "runs.csv")
    requests = _csv(output / "requests.csv")
    _json(output / "summary.json")

    assert manifest["config"]["scenarios_per_seed"] == 3
    assert manifest["config"]["seeds"] == [7, 11]
    assert len(scenarios) == 6
    assert len(runs) == 24
    assert len({(row["seed"], row["scenario_id"]) for row in scenarios}) == 6

    snapshots = {(str(row["seed"]), row["scenario_id"]): row for row in scenarios}
    by_scenario: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in runs:
        by_scenario[(row["seed"], row["scenario_id"])].append(row)
        assert math.isfinite(float(row["solve_latency_ns"]))
        assert int(row["solve_latency_ns"]) >= 0
    assert set(by_scenario) == set(snapshots)
    for key, rows in by_scenario.items():
        assert {row["strategy"] for row in rows} == set(STRATEGIES)
        assert len(rows) == len(STRATEGIES)
        assert {row["variant"] for row in rows} == {"baseline"}
        assert len({row["scenario_hash"] for row in rows}) == 1
        assert rows[0]["scenario_hash"] == snapshots[key]["scenario_hash"]

    request_groups: dict[tuple[str, str, str], int] = defaultdict(int)
    for row in requests:
        request_groups[(row["seed"], row["scenario_id"], row["strategy"])] += 1
    assert set(request_groups) == {
        (seed, scenario_id, strategy)
        for seed, scenario_id in snapshots
        for strategy in STRATEGIES
    }
    for seed, scenario_id in snapshots:
        counts = {request_groups[(seed, scenario_id, strategy)] for strategy in STRATEGIES}
        assert len(counts) == 1
        assert next(iter(counts)) > 0


def test_replay_preserves_inputs_and_non_timing_outcomes(tmp_path: Path) -> None:
    first_dir = tmp_path / "first"
    replay_dir = tmp_path / "replay"
    run_benchmark(_config(first_dir))
    run_benchmark(_config(replay_dir))

    assert (first_dir / "scenarios.jsonl").read_bytes() == (
        replay_dir / "scenarios.jsonl"
    ).read_bytes()
    assert (first_dir / "requests.csv").read_bytes() == (replay_dir / "requests.csv").read_bytes()
    first_runs = _csv(first_dir / "runs.csv")
    replay_runs = _csv(replay_dir / "runs.csv")
    assert len(first_runs) == len(replay_runs)
    for original, replay in zip(first_runs, replay_runs, strict=True):
        original.pop("solve_latency_ns")
        replay.pop("solve_latency_ns")
        assert original == replay


def test_replay_is_independent_of_python_hash_seed(tmp_path: Path) -> None:
    outputs = [tmp_path / "hash_1", tmp_path / "hash_9"]
    for hash_seed, output in zip(("1", "9"), outputs, strict=True):
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "evaluation.run",
                "--scenarios",
                "3",
                "--seeds",
                "19",
                "--strategies",
                "fcfs",
                "nearest",
                "greedy",
                "rescueai",
                "--output-dir",
                str(output),
            ],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "PYTHONHASHSEED": hash_seed},
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
    assert (outputs[0] / "scenarios.jsonl").read_bytes() == (
        outputs[1] / "scenarios.jsonl"
    ).read_bytes()
    assert (outputs[0] / "requests.csv").read_bytes() == (outputs[1] / "requests.csv").read_bytes()
    first_runs = _csv(outputs[0] / "runs.csv")
    other_runs = _csv(outputs[1] / "runs.csv")
    for original, replay in zip(first_runs, other_runs, strict=True):
        original.pop("solve_latency_ns")
        replay.pop("solve_latency_ns")
        assert original == replay


def test_persisted_json_and_csv_do_not_contain_nonfinite_numbers(tmp_path: Path) -> None:
    output = tmp_path / "finite"
    run_benchmark(_config(output, scenarios_per_seed=2, seeds=(1,)))

    _json(output / "manifest.json")
    _json(output / "summary.json")
    _jsonl(output / "scenarios.jsonl")
    for filename in ("runs.csv", "requests.csv"):
        for row in _csv(output / filename):
            assert all(
                value.lower() not in {"nan", "inf", "-inf", "infinity"}
                for value in row.values()
            )


def test_runner_rejects_nonfinite_metrics_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_metrics = getattr(benchmark_runner, "_scenario_metrics")

    def with_nan(*args: Any, **kwargs: Any) -> dict[str, Any]:
        metric: dict[str, Any] = original_metrics(*args, **kwargs)
        metric["coverage_fraction"] = float("nan")
        return metric

    monkeypatch.setattr(benchmark_runner, "_scenario_metrics", with_nan)
    output = tmp_path / "invalid"
    with pytest.raises(ValueError, match="non-finite"):
        run_benchmark(_config(output, scenarios_per_seed=1, seeds=(1,), strategies=("fcfs",)))
    assert not output.exists()


def test_solver_failure_is_recorded_without_inventing_arrivals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_get_strategy = get_strategy

    class FailingStrategy:
        def solve(self, state: Any) -> Any:
            raise RuntimeError("injected strategy failure")

    def injected_failure(name: str) -> Any:
        if name == "fcfs":
            return FailingStrategy()
        return original_get_strategy(name)

    monkeypatch.setattr(benchmark_runner, "get_strategy", injected_failure)
    output = tmp_path / "solver_failure"
    summary = run_benchmark(
        _config(output, scenarios_per_seed=1, seeds=(1,), strategies=("fcfs", "rescueai"))
    )
    runs = _csv(output / "runs.csv")
    requests = _csv(output / "requests.csv")

    assert summary["validation"]["solver_error_runs"] == 1
    assert len(runs) == 2
    failed = next(row for row in runs if row["strategy"] == "fcfs")
    succeeded = next(row for row in runs if row["strategy"] == "rescueai_optimizer")
    assert failed["run_status"] == "solver_error"
    assert failed["error_type"] == "RuntimeError"
    assert failed["error_message"] == "injected strategy failure"
    assert succeeded["run_status"] == "ok"
    failed_requests = [row for row in requests if row["strategy"] == "fcfs"]
    assert failed_requests
    for row in failed_requests:
        assert row["run_status"] == "solver_error"
        assert row["assigned_team_id"] == ""
        assert row["predicted_arrival_at_utc"] == ""
        assert row["predicted_response_seconds"] == ""
        assert json.loads(row["unserved_reasons"]) == ["solver_failure"]
        assert int(row["capped_response_seconds"]) == 7200


def test_uniform_weight_ablation_keeps_review_state_and_evaluation_weights() -> None:
    state = generate_scenarios(seed=5, count=2)[1].state
    uniform = apply_variant(state, "uniform_weights")

    original = {request.request_id: request for request in state.requests}
    for request in uniform.requests:
        source = original[request.request_id]
        assert request.dispatch_weight == (None if source.dispatch_weight is None else 1)
        assert request.evaluation_weight == source.evaluation_weight
        assert request.routing_anchor_accepted == source.routing_anchor_accepted
        assert request.required_capabilities == source.required_capabilities
    assert uniform.feasible_pairs == state.feasible_pairs
    assert original["weight_review"].dispatch_weight is None
    assert any(request.dispatch_weight == 5 for request in state.requests)


def test_ablation_cli_runs_both_variants_on_same_seeded_scenarios(tmp_path: Path) -> None:
    output = tmp_path / "ablation"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "evaluation.run_ablation",
            "--scenarios",
            "2",
            "--seeds",
            "3",
            "--strategies",
            "fcfs",
            "nearest",
            "greedy",
            "rescueai",
            "--output-dir",
            str(output),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    scenarios = _jsonl(output / "scenarios.jsonl")
    runs = _csv(output / "runs.csv")
    assert len(scenarios) == 4
    assert len(runs) == 16
    assert {row["variant"] for row in runs} == {"baseline", "uniform_weights"}
    assert {row["strategy"] for row in runs} == set(STRATEGIES)
    for scenario_id in {row["scenario_id"] for row in runs}:
        for variant in ("baseline", "uniform_weights"):
            selected = [
                row for row in runs
                if row["scenario_id"] == scenario_id and row["variant"] == variant
            ]
            assert len(selected) == 4
            assert len({row["scenario_hash"] for row in selected}) == 1


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"scenarios_per_seed": 0}, "scenario"),
        ({"seeds": ()}, "seed"),
        ({"strategies": ()}, "strateg"),
        ({"horizon_seconds": 0}, "horizon"),
    ],
)
def test_invalid_configuration_is_rejected(
    tmp_path: Path, overrides: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        run_benchmark(_config(tmp_path / "invalid", **overrides))
