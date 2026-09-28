"""Raw routing benchmark output, validation, and seeded-workload checks."""

import csv
import json
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from evaluation.benchmark_routing import (
    CSV_COLUMNS,
    SUMMARY_FILENAME,
    TRIALS_FILENAME,
    RoutingTrial,
    _validate_manifest,
    main,
    run_benchmark,
)


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        assert reader.fieldnames == list(CSV_COLUMNS)
        return list(reader)


def _workload_fields(row: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in row.items() if not key.endswith("runtime_ns")}


def test_runner_saves_every_trial_and_summaries_match_raw_rows(tmp_path: Path) -> None:
    report = run_benchmark(sides=(3, 4), seed=17, repeats=2, warmups=1, output_dir=tmp_path)

    rows = _read_rows(tmp_path / TRIALS_FILENAME)
    on_disk = json.loads((tmp_path / SUMMARY_FILENAME).read_text(encoding="utf-8"))
    assert len(rows) == 4
    assert on_disk == report
    assert report["synthetic"] is True
    assert report["configuration"]["seed"] == 17
    assert report["configuration"]["grid_sides"] == [3, 4]
    assert report["configuration"]["warmups_per_side"] == 1
    assert report["raw_results"]["row_count"] == 4
    assert report["raw_results"]["columns"] == list(CSV_COLUMNS)
    assert report["environment"]["python_version"]

    for row in rows:
        assert all(value is not None and value != "" for value in row.values())
        baseline_edges = json.loads(row["baseline_edge_ids"])
        replanned_edges = json.loads(row["replanned_edge_ids"])
        assert row["blocked_edge_id"] in baseline_edges
        assert row["blocked_edge_id"] not in replanned_edges
        assert int(row["blocked_graph_revision"]) > int(row["baseline_graph_revision"])
        for name in ("route_runtime_ns", "block_runtime_ns", "replan_runtime_ns"):
            assert int(row[name]) >= 0
        for name in (
            "baseline_travel_time_seconds",
            "replanned_travel_time_seconds",
            "baseline_effective_cost_seconds",
            "replanned_effective_cost_seconds",
        ):
            assert math.isfinite(float(row[name]))

    for summary in report["results"]:
        side_rows = [row for row in rows if int(row["grid_side"]) == summary["grid_side"]]
        assert summary["trial_count"] == len(side_rows) == 2
        for name in ("route_runtime_ns", "block_runtime_ns", "replan_runtime_ns"):
            measured = [int(row[name]) for row in side_rows]
            assert summary[name]["count"] == len(measured)
            assert summary[name]["min_ns"] == min(measured)
            assert summary[name]["max_ns"] == max(measured)
            assert summary[name]["mean_ns"] == pytest.approx(sum(measured) / len(measured))


def test_seed_replays_workload_without_asserting_wall_clock_time(tmp_path: Path) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    run_benchmark(sides=(3, 5), seed=9, repeats=2, warmups=0, output_dir=first_dir)
    run_benchmark(sides=(3, 5), seed=9, repeats=2, warmups=0, output_dir=second_dir)

    first = [_workload_fields(row) for row in _read_rows(first_dir / TRIALS_FILENAME)]
    second = [_workload_fields(row) for row in _read_rows(second_dir / TRIALS_FILENAME)]
    assert first == second
    assert [(row["grid_side"], row["trial_index"]) for row in first] == [
        ("3", "0"),
        ("3", "1"),
        ("5", "0"),
        ("5", "1"),
    ]


@pytest.mark.parametrize(
    "invalid",
    [
        {"route_runtime_ns": None},
        {"route_runtime_ns": -1},
        {"baseline_travel_time_seconds": float("nan")},
        {"replanned_effective_cost_seconds": float("inf")},
        {"replanned_edge_ids": ("blocked",)},
        {"blocked_graph_revision": 5},
    ],
)
def test_trial_rejects_missing_nonfinite_and_invalid_route_values(
    invalid: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "grid_side": 3,
        "seed": 9,
        "trial_index": 0,
        "nodes": 9,
        "directed_edges": 24,
        "origin_node_id": "n_000_000",
        "destination_node_id": "n_002_002",
        "blocked_edge_id": "blocked",
        "baseline_edge_ids": ("first", "blocked"),
        "replanned_edge_ids": ("alternate",),
        "baseline_graph_revision": 5,
        "blocked_graph_revision": 6,
        "baseline_travel_time_seconds": 12.0,
        "replanned_travel_time_seconds": 15.0,
        "baseline_effective_cost_seconds": 14.0,
        "replanned_effective_cost_seconds": 17.0,
        "route_runtime_ns": 20,
        "block_runtime_ns": 30,
        "replan_runtime_ns": 40,
    }
    values.update(invalid)

    with pytest.raises(ValidationError):
        RoutingTrial.model_validate(values)


def test_invalid_configuration_creates_no_output(tmp_path: Path) -> None:
    invalid_dir = tmp_path / "invalid"
    for kwargs in (
        {"sides": ()},
        {"sides": (3, 3)},
        {"sides": (1,)},
        {"repeats": 0},
        {"warmups": -1},
    ):
        with pytest.raises(ValueError):
            run_benchmark(output_dir=invalid_dir, **kwargs)
    assert not invalid_dir.exists()


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), " "])
def test_manifest_rejects_missing_or_nonfinite_values(value: object) -> None:
    with pytest.raises(ValueError):
        _validate_manifest({"results": [{"measurement": value}]})


def test_cli_writes_csv_and_json_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        main(
            [
                "--sides",
                "3",
                "--seed",
                "25",
                "--warmups",
                "0",
                "--repeats",
                "1",
                "--output-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    printed = json.loads(capsys.readouterr().out)
    assert printed["trial_count"] == 1
    assert Path(printed["raw_csv"]).is_file()
    assert Path(printed["summary_json"]).is_file()
