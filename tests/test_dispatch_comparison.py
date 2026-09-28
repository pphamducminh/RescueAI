"""Integration checks for the synthetic, single-wave dispatch comparison."""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from evaluation.dispatch_comparison import generate_scenarios, run_comparison


def _without_latency(report: dict[str, Any]) -> dict[str, Any]:
    copy = deepcopy(report)
    for metric in copy["scenario_metrics"]:
        metric.pop("solve_latency_ns")
    return copy


def test_seed_replays_scenario_inputs_and_policy_outcomes(tmp_path: Path) -> None:
    first = run_comparison(
        seed=157,
        scenario_count=5,
        output_prefix=tmp_path / "first",
    )
    replay = run_comparison(
        seed=157,
        scenario_count=5,
        output_prefix=tmp_path / "replay",
    )
    changed = run_comparison(
        seed=158,
        scenario_count=5,
        output_prefix=tmp_path / "changed",
    )

    assert _without_latency(first) == _without_latency(replay)
    assert first["scenario_inputs"] != changed["scenario_inputs"]
    assert first["scenario_inputs"][:2] == changed["scenario_inputs"][:2]
    assert all(metric["solve_latency_ns"] >= 0 for metric in first["scenario_metrics"])


def test_seed_replays_across_python_hash_seeds(tmp_path: Path) -> None:
    """Saved input snapshots and decisions must not depend on set hash order."""

    source = """
import json
import sys
from pathlib import Path
from evaluation.dispatch_comparison import run_comparison

report = run_comparison(
    seed=412073,
    scenario_count=8,
    output_prefix=Path(sys.argv[1]),
)
for metric in report["scenario_metrics"]:
    metric.pop("solve_latency_ns")
print(json.dumps(report, sort_keys=True))
"""
    reports = []
    for hash_seed in ("1", "3"):
        process = subprocess.run(
            [sys.executable, "-c", source, str(tmp_path / f"hash_{hash_seed}")],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "PYTHONHASHSEED": hash_seed},
            check=True,
            capture_output=True,
            text=True,
        )
        reports.append(json.loads(process.stdout))

    assert reports[0] == reports[1]


def test_scenario_generator_is_seeded_and_does_not_share_state() -> None:
    first = generate_scenarios(seed=54, count=4)
    replay = generate_scenarios(seed=54, count=4)
    changed = generate_scenarios(seed=55, count=4)

    assert tuple(item.state.model_dump_json() for item in first) == tuple(
        item.state.model_dump_json() for item in replay
    )
    assert tuple(item.state.model_dump_json() for item in first[2:]) != tuple(
        item.state.model_dump_json() for item in changed[2:]
    )
    assert first[0].state is not replay[0].state


def test_every_strategy_uses_the_same_frozen_request_and_route_snapshot(tmp_path: Path) -> None:
    report = run_comparison(seed=412073, scenario_count=4, output_prefix=tmp_path / "shared")
    strategies = set(report["strategies"])
    by_scenario = {
        item["state"]["scenario_id"]: item["state"] for item in report["scenario_inputs"]
    }
    rows_by_request: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in report["request_metrics"]:
        rows_by_request[(row["scenario_id"], row["request_id"])].append(row)

    assert len(strategies) == 4
    for scenario_id, state in by_scenario.items():
        requests = {item["request_id"]: item for item in state["requests"]}
        pairs = {
            (item["request_id"], item["team_id"]): item for item in state["feasible_pairs"]
        }
        for request_id, request in requests.items():
            rows = rows_by_request[(scenario_id, request_id)]
            assert {row["strategy"] for row in rows} == strategies
            assert len(rows) == len(strategies)
            frozen_fields = (
                "generator_version",
                "seed",
                "scenario_id",
                "scenario_design",
                "scenario_origin",
                "graph_revision",
                "weight_policy_version",
                "request_id",
                "received_at_utc",
                "decision_time_utc",
                "dispatch_weight",
                "evaluation_weight",
                "critical_cue",
                "routing_anchor_accepted",
                "horizon_seconds",
            )
            assert len({tuple(row[field] for field in frozen_fields) for row in rows}) == 1
            assert rows[0]["graph_revision"] == state["graph_revision"]
            assert rows[0]["dispatch_weight"] == request["dispatch_weight"]
            assert rows[0]["evaluation_weight"] == request["evaluation_weight"]
            for row in rows:
                if row["plan_assigned"]:
                    pair = pairs[(request_id, row["assigned_team_id"])]
                    assert row["eta_seconds"] == pair["eta_seconds"]
                    assert row["route_edge_ids"] == pair["route"]["edge_ids"]
                    assert row["route_distance_meters"] == pair["route"]["distance_meters"]
                    arrival = datetime.fromisoformat(row["predicted_arrival_at_utc"])
                    receipt = datetime.fromisoformat(row["received_at_utc"])
                    assert row["predicted_response_seconds"] == pytest.approx(
                        (arrival - receipt).total_seconds()
                    )
                else:
                    assert row["assigned_team_id"] is None
                    assert row["eta_seconds"] is None


def test_json_and_csv_preserve_the_same_per_request_rows(tmp_path: Path) -> None:
    prefix = tmp_path / "formats"
    report = run_comparison(seed=99, scenario_count=3, output_prefix=prefix)
    persisted = json.loads(prefix.with_suffix(".json").read_text(encoding="utf-8"))
    with prefix.with_suffix(".csv").open("r", encoding="utf-8", newline="") as source:
        csv_rows = list(csv.DictReader(source))
    with prefix.with_suffix(".scenarios.csv").open(
        "r", encoding="utf-8", newline=""
    ) as source:
        scenario_csv_rows = list(csv.DictReader(source))

    assert persisted == report
    assert len(csv_rows) == len(report["request_metrics"])
    for csv_row, json_row in zip(csv_rows, report["request_metrics"], strict=True):
        assert set(csv_row) == set(json_row)
        for field, value in json_row.items():
            if isinstance(value, list):
                assert json.loads(csv_row[field]) == value
            elif value is None:
                assert csv_row[field] == ""
            else:
                assert csv_row[field] == str(value)
    assert len(scenario_csv_rows) == len(report["scenario_metrics"])
    for csv_row, json_row in zip(
        scenario_csv_rows, report["scenario_metrics"], strict=True
    ):
        assert set(csv_row) == set(json_row)
        for field, value in json_row.items():
            assert csv_row[field] == ("" if value is None else str(value))


def test_served_means_arrival_within_horizon_from_receipt(tmp_path: Path) -> None:
    report = run_comparison(
        seed=1,
        scenario_count=1,
        horizon_seconds=300,
        output_prefix=tmp_path / "boundary",
    )
    fcfs_rows = {
        row["request_id"]: row
        for row in report["request_metrics"]
        if row["strategy"] == "fcfs"
    }
    fcfs_metric = next(
        item for item in report["scenario_metrics"] if item["strategy"] == "fcfs"
    )

    assert fcfs_rows["critical"]["predicted_response_seconds"] == 300
    assert fcfs_rows["critical"]["reached_by_horizon"] is True
    assert fcfs_rows["other"]["predicted_response_seconds"] == 2460
    assert fcfs_rows["other"]["plan_assigned"] is True
    assert fcfs_rows["other"]["reached_by_horizon"] is False
    assert fcfs_rows["other"]["capped_response_seconds"] == 300
    assert fcfs_metric["plan_assigned_count"] == 2
    assert fcfs_metric["reached_by_horizon_count"] == 1
    assert fcfs_metric["unreached_by_horizon_count"] == 1
    assert fcfs_metric["coverage_fraction"] == 0.5
    assert fcfs_metric["mean_response_seconds_reached"] == 300

    earlier = run_comparison(
        seed=1,
        scenario_count=1,
        horizon_seconds=299,
        output_prefix=tmp_path / "earlier",
    )
    earlier_fcfs = next(
        item for item in earlier["scenario_metrics"] if item["strategy"] == "fcfs"
    )
    assert earlier_fcfs["reached_by_horizon_count"] == 0
    assert earlier_fcfs["mean_response_seconds_reached"] is None
    assert earlier_fcfs["mean_critical_response_seconds_reached"] is None


def test_unassigned_requests_are_unreached_with_explicit_reasons(tmp_path: Path) -> None:
    report = run_comparison(
        seed=1,
        scenario_count=2,
        horizon_seconds=3600,
        output_prefix=tmp_path / "pending",
    )
    rows = [
        row
        for row in report["request_metrics"]
        if row["scenario_id"] == "synthetic-0001" and row["strategy"] == "fcfs"
    ]
    by_request = {row["request_id"]: row for row in rows}

    for request_id, expected_reason in (
        ("blocked", "no_eligible_route"),
        ("anchor_review", "location_pending_review"),
        ("weight_review", "weight_pending_review"),
    ):
        row = by_request[request_id]
        assert row["plan_assigned"] is False
        assert row["reached_by_horizon"] is False
        assert row["capped_response_seconds"] == 3600
        assert expected_reason in row["unserved_reasons"]


@pytest.mark.parametrize("horizon", [0, -1])
def test_comparison_requires_a_positive_horizon(horizon: int, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="horizon"):
        run_comparison(
            seed=1,
            scenario_count=1,
            horizon_seconds=horizon,
            output_prefix=tmp_path / "invalid",
        )
