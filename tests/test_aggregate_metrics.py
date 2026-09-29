"""Raw dispatch benchmark aggregation is paired and rejects invalid data."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any

import pytest

from evaluation.aggregate_metrics import aggregate_metrics
from evaluation.dispatch_comparison import run_comparison


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    name: json.dumps(value) if isinstance(value, list) else value
                    for name, value in row.items()
                }
            )


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _raw_directory(tmp_path: Path, *, scenario_count: int = 2) -> Path:
    report = run_comparison(
        seed=17,
        scenario_count=scenario_count,
        output_prefix=tmp_path / "legacy",
    )
    raw = tmp_path / "raw"
    raw.mkdir()
    strategies = report["strategies"]
    config = {
        "scenarios_per_seed": scenario_count,
        "seeds": [17],
        "strategies": strategies,
        "variants": ["baseline"],
        "horizon_seconds": report["horizon_seconds"],
    }
    (raw / "manifest.json").write_text(
        json.dumps({"config": config, "synthetic": True}), encoding="utf-8"
    )
    scenario_hashes = {
        item["state"]["scenario_id"]: hashlib.sha256(
            json.dumps(
                item["state"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        for item in report["scenario_inputs"]
    }
    with (raw / "scenarios.jsonl").open("w", encoding="utf-8") as handle:
        for item in report["scenario_inputs"]:
            state = item["state"]
            scenario_id = state["scenario_id"]
            handle.write(
                json.dumps(
                    {
                        "seed": 17,
                        "scenario_id": scenario_id,
                        "variant": "baseline",
                        "scenario_origin": item["origin"],
                        "scenario_design": item["design"],
                        "source_scenario_hash": scenario_hashes[scenario_id],
                        "scenario_hash": scenario_hashes[scenario_id],
                        "state": state,
                    }
                )
                + "\n"
            )
    run_rows: list[dict[str, Any]] = []
    for metric in report["scenario_metrics"]:
        request_rows = [
            row
            for row in report["request_metrics"]
            if row["scenario_id"] == metric["scenario_id"] and row["strategy"] == metric["strategy"]
        ]
        critical = [row["capped_response_seconds"] for row in request_rows if row["critical_cue"]]
        capped = sorted(row["capped_response_seconds"] for row in request_rows)
        scenario_id = metric["scenario_id"]
        run_rows.append(
            {
                **metric,
                "variant": "baseline",
                "source_scenario_hash": scenario_hashes[scenario_id],
                "scenario_hash": scenario_hashes[scenario_id],
                "run_status": "ok",
                "error_type": "",
                "error_message": "",
                "critical_capped_all_request_seconds": (
                    statistics.fmean(critical) if critical else None
                ),
                "p90_capped_all_request_seconds": capped[math.ceil(0.9 * len(capped)) - 1],
            }
        )
    request_rows = [
        {
            **row,
            "variant": "baseline",
            "source_scenario_hash": scenario_hashes[row["scenario_id"]],
            "scenario_hash": scenario_hashes[row["scenario_id"]],
            "run_status": "ok",
            "error_type": "",
            "error_message": "",
        }
        for row in report["request_metrics"]
    ]
    _write_csv(raw / "runs.csv", run_rows)
    _write_csv(raw / "requests.csv", request_rows)
    return raw


def test_aggregates_scenario_runs_and_preserves_raw_files(tmp_path: Path) -> None:
    raw = _raw_directory(tmp_path)
    before = {
        name: (raw / name).read_bytes()
        for name in ("manifest.json", "scenarios.jsonl", "runs.csv", "requests.csv")
    }

    summary = aggregate_metrics(raw)

    assert summary["validation"] == {
        "paired_strategy_coverage": True,
        "scenario_records": 2,
        "run_rows": 8,
        "request_rows": 28,
        "solver_error_runs": 0,
    }
    assert len(summary["groups"]) == 4
    for group in summary["groups"]:
        assert group["scenario_count"] == 2
        assert group["metrics"]["coverage_fraction"]["count"] == 2
        assert group["metrics"]["solve_latency_ns"]["min"] >= 0
    assert json.loads((raw / "summary.json").read_text(encoding="utf-8")) == summary
    assert before == {name: (raw / name).read_bytes() for name in before}


def test_aggregation_uses_scenarios_not_requests_as_replicates(tmp_path: Path) -> None:
    raw = _raw_directory(tmp_path)
    run_rows = _read_csv(raw / "runs.csv")
    fcfs_coverage = [
        float(row["coverage_fraction"]) for row in run_rows if row["strategy"] == "fcfs"
    ]

    summary = aggregate_metrics(raw)

    fcfs = next(group for group in summary["groups"] if group["strategy"] == "fcfs")
    assert fcfs["metrics"]["coverage_fraction"]["mean"] == pytest.approx(
        statistics.fmean(fcfs_coverage)
    )
    assert fcfs["metrics"]["coverage_fraction"]["count"] == 2


def test_rejects_missing_paired_strategy_run(tmp_path: Path) -> None:
    raw = _raw_directory(tmp_path)
    rows = _read_csv(raw / "runs.csv")
    _write_csv(raw / "runs.csv", rows[:-1])

    with pytest.raises(ValueError, match="missing paired strategy runs"):
        aggregate_metrics(raw)
    assert not (raw / "summary.json").exists()


def test_rejects_duplicate_request_id(tmp_path: Path) -> None:
    raw = _raw_directory(tmp_path)
    rows = _read_csv(raw / "requests.csv")
    _write_csv(raw / "requests.csv", [*rows, rows[0]])

    with pytest.raises(ValueError, match="request row count|duplicate request"):
        aggregate_metrics(raw)


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("coverage_fraction", "NaN", "non-finite"),
        ("coverage_fraction", "", "required finite number"),
        ("mean_response_seconds_reached", "", "required finite number"),
    ],
)
def test_rejects_nan_or_missing_nonnullable_metric(
    tmp_path: Path, field: str, replacement: str, message: str
) -> None:
    raw = _raw_directory(tmp_path)
    rows = _read_csv(raw / "runs.csv")
    rows[0][field] = replacement
    _write_csv(raw / "runs.csv", rows)

    with pytest.raises(ValueError, match=message):
        aggregate_metrics(raw)


def test_rejects_metric_that_disagrees_with_raw_requests(tmp_path: Path) -> None:
    raw = _raw_directory(tmp_path)
    rows = _read_csv(raw / "runs.csv")
    rows[0]["coverage_fraction"] = "0.1"
    _write_csv(raw / "runs.csv", rows)

    with pytest.raises(ValueError, match="coverage_fraction.*does not match"):
        aggregate_metrics(raw)


def test_solver_error_remains_in_summary_with_zero_coverage(tmp_path: Path) -> None:
    raw = _raw_directory(tmp_path, scenario_count=1)
    run_rows = _read_csv(raw / "runs.csv")
    request_rows = _read_csv(raw / "requests.csv")
    failed = next(row for row in run_rows if row["strategy"] == "fcfs")
    failed.update(
        {
            "run_status": "solver_error",
            "error_type": "SyntheticSolverFailure",
            "error_message": "fixture failure",
            "plan_assigned_count": "0",
            "reached_by_horizon_count": "0",
            "unreached_by_horizon_count": failed["request_count"],
            "coverage_fraction": "0.0",
            "critical_cue_reached_count": "0",
            "critical_cue_unreached_fraction": "1.0",
            "mean_response_seconds_reached": "",
            "mean_critical_response_seconds_reached": "",
            "evaluation_weighted_mean_response_seconds_reached": "",
            "evaluation_weighted_capped_all_request_seconds": "3600.0",
            "critical_capped_all_request_seconds": "3600.0",
            "p90_capped_all_request_seconds": "3600.0",
            "planned_route_distance_meters": "0.0",
            "reached_route_distance_meters": "0.0",
            "objective_P": "",
            "objective_Q_seconds": "",
            "objective_K_seconds": "",
            "objective_C": "",
            "plan_status": "",
            "solver_status": "ERROR",
        }
    )
    for row in request_rows:
        if row["strategy"] != "fcfs":
            continue
        row.update(
            {
                "run_status": "solver_error",
                "error_type": "SyntheticSolverFailure",
                "error_message": "fixture failure",
                "assigned_team_id": "",
                "plan_assigned": "False",
                "reached_by_horizon": "False",
                "unserved_reasons": '["solver_failure"]',
                "eta_seconds": "",
                "predicted_arrival_at_utc": "",
                "predicted_response_seconds": "",
                "route_distance_meters": "",
                "route_edge_ids": "[]",
                "capped_response_seconds": "3600",
                "objective_P": "",
                "objective_Q_seconds": "",
                "objective_K_seconds": "",
                "objective_C": "",
                "plan_status": "",
                "solver_status": "ERROR",
            }
        )
    _write_csv(raw / "runs.csv", run_rows)
    _write_csv(raw / "requests.csv", request_rows)

    summary = aggregate_metrics(raw)

    assert summary["validation"]["solver_error_runs"] == 1
    assert summary["failures"][0]["strategy"] == "fcfs"
    fcfs = next(group for group in summary["groups"] if group["strategy"] == "fcfs")
    assert fcfs["solver_error_count"] == 1
    assert fcfs["metrics"]["coverage_fraction"]["mean"] == 0
    assert fcfs["metrics"]["mean_response_seconds_reached"]["null_count"] == 1
