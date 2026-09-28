"""Validate raw dispatch benchmark output and summarize scenario-level metrics.

Each scenario is one observation. Requests are checked against their scenario
snapshot, but are never treated as independent observations in the summary.
Control and seeded scenarios are grouped separately. A solver failure remains
an observed run with zero horizon coverage and an explicit failure count.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

from pydantic import ValidationError

from backend.dispatch import DispatchState

ScenarioKey = tuple[int, str, str]
RunKey = tuple[int, str, str, str]
CsvRow = dict[str, str]

IDENTITY_FIELDS = (
    "seed",
    "scenario_id",
    "variant",
    "scenario_origin",
    "scenario_design",
    "source_scenario_hash",
    "scenario_hash",
)
RUN_REQUIRED_FIELDS = set(IDENTITY_FIELDS) | {
    "strategy",
    "run_status",
    "error_type",
    "error_message",
    "request_count",
    "plan_assigned_count",
    "reached_by_horizon_count",
    "unreached_by_horizon_count",
    "coverage_fraction",
    "critical_cue_count",
    "critical_cue_reached_count",
    "critical_cue_unreached_fraction",
    "mean_response_seconds_reached",
    "mean_critical_response_seconds_reached",
    "evaluation_weighted_mean_response_seconds_reached",
    "evaluation_weighted_capped_all_request_seconds",
    "critical_capped_all_request_seconds",
    "p90_capped_all_request_seconds",
    "planned_route_distance_meters",
    "reached_route_distance_meters",
    "objective_P",
    "objective_Q_seconds",
    "objective_K_seconds",
    "objective_C",
    "solve_latency_ns",
    "graph_revision",
    "weight_policy_version",
    "plan_status",
    "solver_status",
}
REQUEST_REQUIRED_FIELDS = set(IDENTITY_FIELDS) | {
    "generator_version",
    "strategy",
    "run_status",
    "error_type",
    "error_message",
    "request_id",
    "graph_revision",
    "weight_policy_version",
    "received_at_utc",
    "decision_time_utc",
    "dispatch_weight",
    "critical_cue",
    "evaluation_weight",
    "routing_anchor_accepted",
    "plan_assigned",
    "reached_by_horizon",
    "assigned_team_id",
    "eta_seconds",
    "predicted_arrival_at_utc",
    "predicted_response_seconds",
    "route_distance_meters",
    "route_edge_ids",
    "capped_response_seconds",
    "horizon_seconds",
    "unserved_reasons",
    "plan_status",
    "solver_status",
    "objective_P",
    "objective_Q_seconds",
    "objective_K_seconds",
    "objective_C",
}
RUN_INTEGER_FIELDS = {
    "request_count",
    "plan_assigned_count",
    "reached_by_horizon_count",
    "unreached_by_horizon_count",
    "critical_cue_count",
    "critical_cue_reached_count",
    "objective_P",
    "objective_Q_seconds",
    "objective_K_seconds",
    "objective_C",
    "solve_latency_ns",
}
RUN_FLOAT_FIELDS = {
    "coverage_fraction",
    "critical_cue_unreached_fraction",
    "mean_response_seconds_reached",
    "mean_critical_response_seconds_reached",
    "evaluation_weighted_mean_response_seconds_reached",
    "evaluation_weighted_capped_all_request_seconds",
    "critical_capped_all_request_seconds",
    "p90_capped_all_request_seconds",
    "planned_route_distance_meters",
    "reached_route_distance_meters",
}
REACHED_ONLY_FIELDS = {
    "mean_response_seconds_reached",
    "mean_critical_response_seconds_reached",
    "evaluation_weighted_mean_response_seconds_reached",
}
OBJECTIVE_FIELDS = {
    "objective_P",
    "objective_Q_seconds",
    "objective_K_seconds",
    "objective_C",
}
AGGREGATED_METRICS = (
    "request_count",
    "plan_assigned_count",
    "reached_by_horizon_count",
    "coverage_fraction",
    "critical_cue_unreached_fraction",
    "mean_response_seconds_reached",
    "mean_critical_response_seconds_reached",
    "evaluation_weighted_mean_response_seconds_reached",
    "evaluation_weighted_capped_all_request_seconds",
    "critical_capped_all_request_seconds",
    "p90_capped_all_request_seconds",
    "planned_route_distance_meters",
    "reached_route_distance_meters",
    "solve_latency_ns",
)


def _fail(message: str) -> NoReturn:
    raise ValueError(message)


def _nonfinite_json(value: str) -> None:
    _fail(f"non-finite JSON value: {value}")


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle, parse_constant=_nonfinite_json)
    _ensure_finite(value, path.name)
    return value


def _ensure_finite(value: Any, label: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        _fail(f"{label}: non-finite number")
    if isinstance(value, dict):
        for name, child in value.items():
            _ensure_finite(child, f"{label}.{name}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _ensure_finite(child, f"{label}[{index}]")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                _fail(f"{path.name}:{line_number}: blank JSONL record")
            value = json.loads(line, parse_constant=_nonfinite_json)
            if not isinstance(value, dict):
                _fail(f"{path.name}:{line_number}: expected an object")
            _ensure_finite(value, f"{path.name}:{line_number}")
            records.append(value)
    return records


def _read_csv(path: Path, required: set[str]) -> list[CsvRow]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames
        if headers is None:
            _fail(f"{path.name}: missing CSV headers")
        if len(headers) != len(set(headers)):
            _fail(f"{path.name}: missing or duplicate CSV headers")
        missing = required - set(headers)
        if missing:
            _fail(f"{path.name}: missing columns {sorted(missing)}")
        rows: list[CsvRow] = []
        for line_number, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                _fail(f"{path.name}:{line_number}: malformed CSV row")
            for name, value in row.items():
                if value.strip().lower() in {
                    "nan",
                    "+nan",
                    "-nan",
                    "inf",
                    "+inf",
                    "-inf",
                    "infinity",
                    "+infinity",
                    "-infinity",
                }:
                    _fail(f"{path.name}:{line_number}: non-finite {name}")
            rows.append({name: value for name, value in row.items() if name is not None})
    return rows


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{label}: required non-empty text")
    return value


def _integer(value: Any, label: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool):
        _fail(f"{label}: expected integer")
    if isinstance(value, str):
        if not value or value.strip() != value:
            _fail(f"{label}: expected integer")
        try:
            result = int(value)
        except ValueError:
            _fail(f"{label}: expected integer")
    elif isinstance(value, int):
        result = value
    else:
        _fail(f"{label}: expected integer")
    if minimum is not None and result < minimum:
        _fail(f"{label}: must be at least {minimum}")
    return result


def _number(value: Any, label: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or value == "":
        _fail(f"{label}: required finite number")
    try:
        result = float(value)
    except (TypeError, ValueError):
        _fail(f"{label}: required finite number")
    if not math.isfinite(result):
        _fail(f"{label}: non-finite number")
    if minimum is not None and result < minimum:
        _fail(f"{label}: must be at least {minimum}")
    return result


def _boolean(value: str, label: str) -> bool:
    if value.lower() in {"true", "1"}:
        return True
    if value.lower() in {"false", "0"}:
        return False
    _fail(f"{label}: expected boolean")


def _timestamp(value: str, label: str) -> datetime:
    if not value:
        _fail(f"{label}: required timestamp")
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        _fail(f"{label}: invalid ISO timestamp")
    if result.tzinfo is None or result.utcoffset() is None:
        _fail(f"{label}: timezone is required")
    return result


def _close(actual: float, expected: float, label: str) -> None:
    if not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-7):
        _fail(f"{label}: {actual} does not match raw request value {expected}")


def _present_metric(metrics: dict[str, int | float | None], field: str) -> float:
    value = metrics[field]
    if value is None:
        _fail(f"{field}: expected a numeric value")
    return float(value)


def _manifest(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = _read_json(path)
    if not isinstance(manifest, dict):
        _fail("manifest.json: expected an object")
    config = manifest.get("config", manifest)
    if not isinstance(config, dict):
        _fail("manifest.json: config must be an object")
    _integer(config.get("scenarios_per_seed"), "config.scenarios_per_seed", minimum=1)
    _integer(config.get("horizon_seconds"), "config.horizon_seconds", minimum=1)
    seeds = config.get("seeds")
    strategies = config.get("strategies")
    variants = config.get("variants")
    if not isinstance(seeds, list) or not seeds:
        _fail("config.seeds: expected a non-empty list")
    if not isinstance(strategies, list) or not strategies:
        _fail("config.strategies: expected a non-empty list")
    if not isinstance(variants, list) or not variants:
        _fail("config.variants: expected a non-empty list")
    seed_values = [_integer(seed, "config.seeds[]") for seed in seeds]
    strategy_values = [_required_text(name, "config.strategies[]") for name in strategies]
    variant_values = [_required_text(name, "config.variants[]") for name in variants]
    if any(
        len(values) != len(set(values)) for values in (seed_values, strategy_values, variant_values)
    ):
        _fail("manifest.json: seeds, strategies, and variants must be unique")
    return manifest, config


def _scenario_key(row: dict[str, Any], label: str) -> ScenarioKey:
    return (
        _integer(row.get("seed"), f"{label}.seed"),
        _required_text(row.get("scenario_id"), f"{label}.scenario_id"),
        _required_text(row.get("variant"), f"{label}.variant"),
    )


def _run_key(row: dict[str, Any], label: str) -> RunKey:
    return (*_scenario_key(row, label), _required_text(row.get("strategy"), f"{label}.strategy"))


def _validate_identity(row: dict[str, Any], scenario: dict[str, Any], label: str) -> None:
    for field in IDENTITY_FIELDS:
        if str(row.get(field)) != str(scenario.get(field)):
            _fail(f"{label}: {field} does not match scenario snapshot")


def _validate_scenarios(
    records: list[dict[str, Any]], config: dict[str, Any]
) -> dict[ScenarioKey, dict[str, Any]]:
    scenarios: dict[ScenarioKey, dict[str, Any]] = {}
    seeds = set(config["seeds"])
    variants = set(config["variants"])
    source_hashes: dict[tuple[int, str], str] = {}
    ids_by_seed_variant: dict[tuple[int, str], set[str]] = defaultdict(set)
    for index, record in enumerate(records, start=1):
        label = f"scenarios.jsonl:{index}"
        key = _scenario_key(record, label)
        if key in scenarios:
            _fail(f"{label}: duplicate scenario {key}")
        if key[0] not in seeds or key[2] not in variants:
            _fail(f"{label}: scenario seed or variant is not configured")
        for field in (
            "scenario_origin",
            "scenario_design",
            "source_scenario_hash",
            "scenario_hash",
        ):
            _required_text(record.get(field), f"{label}.{field}")
        state = record.get("state")
        if not isinstance(state, dict) or state.get("scenario_id") != key[1]:
            _fail(f"{label}: invalid state or scenario ID")
        try:
            validated_state = DispatchState.model_validate(state)
        except ValidationError as error:
            _fail(f"{label}: invalid dispatch state: {error}")
        if validated_state.model_dump(mode="json") != state:
            _fail(f"{label}: state snapshot is not a canonical schema serialization")
        encoded = json.dumps(
            state, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        if hashlib.sha256(encoded).hexdigest() != record["scenario_hash"]:
            _fail(f"{label}: scenario hash does not match state snapshot")
        if key[2] == "baseline" and record["source_scenario_hash"] != record["scenario_hash"]:
            _fail(f"{label}: baseline source hash differs from scenario hash")
        requests = state.get("requests")
        if not isinstance(requests, list) or not requests:
            _fail(f"{label}: state must have at least one request")
        request_ids = [
            _required_text(
                item.get("request_id") if isinstance(item, dict) else None, f"{label}.request_id"
            )
            for item in requests
        ]
        if len(request_ids) != len(set(request_ids)):
            _fail(f"{label}: duplicate request ID")
        source_key = (key[0], key[1])
        previous_hash = source_hashes.setdefault(source_key, record["source_scenario_hash"])
        if previous_hash != record["source_scenario_hash"]:
            _fail(f"{label}: variants do not share the source scenario hash")
        ids_by_seed_variant[(key[0], key[2])].add(key[1])
        scenarios[key] = record
    expected_count = config["scenarios_per_seed"] * len(config["seeds"]) * len(config["variants"])
    if len(scenarios) != expected_count:
        _fail(f"scenarios.jsonl: expected {expected_count} scenarios, found {len(scenarios)}")
    for seed in config["seeds"]:
        reference: set[str] | None = None
        for variant in config["variants"]:
            ids = ids_by_seed_variant[(seed, variant)]
            if len(ids) != config["scenarios_per_seed"]:
                _fail(f"scenarios.jsonl: wrong count for seed={seed}, variant={variant}")
            if reference is not None and ids != reference:
                _fail(f"scenarios.jsonl: variants have different scenario IDs for seed={seed}")
            reference = ids
    return scenarios


def _parse_run_metrics(row: CsvRow, label: str) -> dict[str, int | float | None]:
    status = row["run_status"]
    if status not in {"ok", "solver_error"}:
        _fail(f"{label}: invalid run_status")
    parsed: dict[str, int | float | None] = {}
    for field in RUN_INTEGER_FIELDS:
        value = row[field]
        if value == "" and status == "solver_error" and field in OBJECTIVE_FIELDS:
            parsed[field] = None
        else:
            parsed[field] = _integer(value, f"{label}.{field}", minimum=0)
    for field in RUN_FLOAT_FIELDS:
        value = row[field]
        denominator_zero = (
            (
                field
                in {
                    "mean_response_seconds_reached",
                    "evaluation_weighted_mean_response_seconds_reached",
                }
                and parsed["reached_by_horizon_count"] == 0
            )
            or (
                field == "mean_critical_response_seconds_reached"
                and parsed["critical_cue_reached_count"] == 0
            )
            or (
                field in {"critical_cue_unreached_fraction", "critical_capped_all_request_seconds"}
                and parsed["critical_cue_count"] == 0
            )
        )
        if value == "" and denominator_zero:
            parsed[field] = None
        elif value == "" and status == "solver_error" and field in REACHED_ONLY_FIELDS:
            parsed[field] = None
        else:
            parsed[field] = _number(value, f"{label}.{field}", minimum=0)
            if denominator_zero:
                _fail(f"{label}.{field}: must be empty when denominator is zero")
    if status == "ok" and (row["error_type"] or row["error_message"]):
        _fail(f"{label}: successful run has error fields")
    if status == "ok":
        _required_text(row["plan_status"], f"{label}.plan_status")
        _required_text(row["solver_status"], f"{label}.solver_status")
        k = _integer(row["objective_K_seconds"], f"{label}.objective_K_seconds", minimum=1)
        p = _integer(row["objective_P"], f"{label}.objective_P", minimum=0)
        q = _integer(row["objective_Q_seconds"], f"{label}.objective_Q_seconds", minimum=0)
        c = _integer(row["objective_C"], f"{label}.objective_C", minimum=0)
        if c != k * p + q:
            _fail(f"{label}: objective C does not equal K*P+Q")
    if status == "solver_error":
        _required_text(row["error_type"], f"{label}.error_type")
        if row["plan_status"] or row["solver_status"] != "ERROR":
            _fail(f"{label}: solver error plan/solver status is inconsistent")
        if any(parsed[field] is not None for field in OBJECTIVE_FIELDS):
            _fail(f"{label}: solver error must have null objective")
    return parsed


def _request_boolean(row: CsvRow, field: str, label: str) -> bool:
    return _boolean(row[field], f"{label}.{field}")


def _validate_request_rows(
    key: RunKey,
    run: CsvRow,
    metrics: dict[str, int | float | None],
    rows: list[CsvRow],
    scenario: dict[str, Any],
    horizon: int,
) -> None:
    label = f"run {key}"
    state = scenario["state"]
    requests = {request["request_id"]: request for request in state["requests"]}
    pairs = {(pair["request_id"], pair["team_id"]): pair for pair in state["feasible_pairs"]}
    decision_time = _timestamp(state["decision_time"], f"{label}.decision_time")
    if _integer(run["graph_revision"], f"{label}.graph_revision") != state["graph_revision"]:
        _fail(f"{label}: run graph revision differs from scenario snapshot")
    if run["weight_policy_version"] != state["weight_policy_version"]:
        _fail(f"{label}: run weight policy differs from scenario snapshot")
    if len(rows) != len(requests) or metrics["request_count"] != len(requests):
        _fail(f"{label}: request row count does not match scenario snapshot")
    seen: set[str] = set()
    reached_values: list[float] = []
    critical_reached_values: list[float] = []
    weighted_reached: list[float] = []
    reached_weights = 0
    capped_values: list[float] = []
    critical_capped: list[float] = []
    weighted_capped: list[float] = []
    all_weights = 0
    planned_distance = 0.0
    reached_distance = 0.0
    assigned_count = 0
    reached_count = 0
    critical_count = 0
    critical_reached_count = 0
    for row in rows:
        request_id = _required_text(row["request_id"], f"{label}.request_id")
        if request_id not in requests or request_id in seen:
            _fail(f"{label}: unknown or duplicate request {request_id}")
        seen.add(request_id)
        request = requests[request_id]
        _required_text(row["generator_version"], f"{label}.generator_version")
        if _integer(row["graph_revision"], f"{label}.graph_revision") != state["graph_revision"]:
            _fail(f"{label}: request graph revision differs from scenario snapshot")
        if row["weight_policy_version"] != state["weight_policy_version"]:
            _fail(f"{label}: request weight policy differs from scenario snapshot")
        if _timestamp(row["decision_time_utc"], f"{label}.decision_time_utc") != decision_time:
            _fail(f"{label}: decision time differs from scenario snapshot")
        received_at = _timestamp(row["received_at_utc"], f"{label}.received_at_utc")
        if received_at != _timestamp(request["received_at"], f"{label}.received_at"):
            _fail(f"{label}: receipt time differs from scenario snapshot")
        expected_dispatch_weight = request["dispatch_weight"]
        if expected_dispatch_weight is None:
            if row["dispatch_weight"]:
                _fail(f"{label}: unknown dispatch weight must remain empty")
        elif (
            _integer(row["dispatch_weight"], f"{label}.dispatch_weight") != expected_dispatch_weight
        ):
            _fail(f"{label}: dispatch weight differs from scenario snapshot")
        if (
            _request_boolean(row, "routing_anchor_accepted", label)
            != request["routing_anchor_accepted"]
        ):
            _fail(f"{label}: routing anchor acceptance differs from scenario snapshot")
        if (
            row["run_status"] != run["run_status"]
            or row["error_type"] != run["error_type"]
            or row["error_message"] != run["error_message"]
        ):
            _fail(f"{label}: request status/error does not match run")
        if row["plan_status"] != run["plan_status"] or row["solver_status"] != run["solver_status"]:
            _fail(f"{label}: request plan/solver status does not match run")
        critical = _request_boolean(row, "critical_cue", label)
        if critical != request["critical_cue"]:
            _fail(f"{label}: critical cue differs from scenario snapshot")
        weight = _integer(row["evaluation_weight"], f"{label}.evaluation_weight", minimum=1)
        if weight != request["evaluation_weight"]:
            _fail(f"{label}: evaluation weight differs from scenario snapshot")
        if _integer(row["horizon_seconds"], f"{label}.horizon_seconds") != horizon:
            _fail(f"{label}: request horizon differs from config")
        assigned = _request_boolean(row, "plan_assigned", label)
        reached = _request_boolean(row, "reached_by_horizon", label)
        capped = _number(
            row["capped_response_seconds"], f"{label}.capped_response_seconds", minimum=0
        )
        if capped > horizon:
            _fail(f"{label}: capped response exceeds horizon")
        response: float | None = None
        distance: float | None = None
        try:
            route_edges = json.loads(row["route_edge_ids"], parse_constant=_nonfinite_json)
        except json.JSONDecodeError:
            _fail(f"{label}: invalid route_edge_ids JSON")
        if not isinstance(route_edges, list) or any(
            not isinstance(edge, str) for edge in route_edges
        ):
            _fail(f"{label}: invalid route_edge_ids")
        if assigned:
            assigned_count += 1
            team_id = _required_text(row["assigned_team_id"], f"{label}.assigned_team_id")
            pair = pairs.get((request_id, team_id))
            if pair is None:
                _fail(f"{label}: assignment is not feasible in the saved scenario")
            eta = _integer(row["eta_seconds"], f"{label}.eta_seconds", minimum=0)
            if eta != pair["eta_seconds"]:
                _fail(f"{label}: ETA differs from saved feasible route")
            response = _number(
                row["predicted_response_seconds"], f"{label}.predicted_response_seconds", minimum=0
            )
            distance = _number(
                row["route_distance_meters"], f"{label}.route_distance_meters", minimum=0
            )
            if route_edges != pair["route"]["edge_ids"]:
                _fail(f"{label}: route edges differ from saved feasible route")
            _close(
                distance,
                pair["route"]["distance_meters"],
                f"{label}.route_distance_meters",
            )
            arrival = _timestamp(
                row["predicted_arrival_at_utc"], f"{label}.predicted_arrival_at_utc"
            )
            _close(
                (arrival - decision_time).total_seconds(),
                pair["route"]["travel_time_seconds"],
                f"{label}.predicted_arrival_at_utc",
            )
            _close(
                response,
                (arrival - received_at).total_seconds(),
                f"{label}.predicted_response_seconds",
            )
            _close(capped, min(response, horizon), f"{label}.capped_response_seconds")
            planned_distance += distance
        elif (
            row["assigned_team_id"]
            or row["eta_seconds"]
            or row["predicted_arrival_at_utc"]
            or row["predicted_response_seconds"]
            or row["route_distance_meters"]
            or route_edges
        ):
            _fail(f"{label}: unassigned request has assignment/arrival fields")
        else:
            _close(capped, horizon, f"{label}.capped_response_seconds")
        if reached != (response is not None and response <= horizon):
            _fail(f"{label}: reached flag disagrees with predicted response")
        if reached:
            assert response is not None and distance is not None
            reached_count += 1
            reached_values.append(response)
            weighted_reached.append(weight * response)
            reached_weights += weight
            reached_distance += distance
        if critical:
            critical_count += 1
            critical_capped.append(capped)
            if reached:
                assert response is not None
                critical_reached_count += 1
                critical_reached_values.append(response)
        capped_values.append(capped)
        weighted_capped.append(weight * capped)
        all_weights += weight
        try:
            reasons = json.loads(row["unserved_reasons"], parse_constant=_nonfinite_json)
        except json.JSONDecodeError:
            _fail(f"{label}: invalid unserved_reasons JSON")
        if not isinstance(reasons, list) or any(not isinstance(item, str) for item in reasons):
            _fail(f"{label}: invalid unserved_reasons")
        if run["run_status"] == "solver_error":
            if assigned or reasons != ["solver_failure"]:
                _fail(f"{label}: solver failure request must have solver_failure reason")
        elif assigned and reasons:
            _fail(f"{label}: assigned request has unserved reasons")
        elif not assigned and not reasons:
            _fail(f"{label}: unassigned request has no reason")
        for field in OBJECTIVE_FIELDS:
            if row.get(field, "") != run[field]:
                _fail(f"{label}: request {field} differs from scenario run")
    if seen != set(requests):
        _fail(f"{label}: request IDs do not match scenario snapshot")
    if (
        assigned_count != metrics["plan_assigned_count"]
        or reached_count != metrics["reached_by_horizon_count"]
    ):
        _fail(f"{label}: assigned/reached count disagrees with request rows")
    if len(rows) - reached_count != metrics["unreached_by_horizon_count"]:
        _fail(f"{label}: unreached count disagrees with request rows")
    if (
        critical_count != metrics["critical_cue_count"]
        or critical_reached_count != metrics["critical_cue_reached_count"]
    ):
        _fail(f"{label}: critical counts disagree with request rows")
    _close(
        _present_metric(metrics, "coverage_fraction"),
        reached_count / len(rows),
        f"{label}.coverage_fraction",
    )
    _close(
        _present_metric(metrics, "evaluation_weighted_capped_all_request_seconds"),
        math.fsum(weighted_capped) / all_weights,
        f"{label}.evaluation_weighted_capped_all_request_seconds",
    )
    _close(
        _present_metric(metrics, "p90_capped_all_request_seconds"),
        sorted(capped_values)[math.ceil(0.9 * len(rows)) - 1],
        f"{label}.p90_capped_all_request_seconds",
    )
    _close(
        _present_metric(metrics, "planned_route_distance_meters"),
        planned_distance,
        f"{label}.planned_route_distance_meters",
    )
    _close(
        _present_metric(metrics, "reached_route_distance_meters"),
        reached_distance,
        f"{label}.reached_route_distance_meters",
    )
    expected_optional = {
        "critical_cue_unreached_fraction": (critical_count - critical_reached_count)
        / critical_count
        if critical_count
        else None,
        "critical_capped_all_request_seconds": statistics.fmean(critical_capped)
        if critical_capped
        else None,
        "mean_response_seconds_reached": statistics.fmean(reached_values)
        if reached_values
        else None,
        "mean_critical_response_seconds_reached": statistics.fmean(critical_reached_values)
        if critical_reached_values
        else None,
        "evaluation_weighted_mean_response_seconds_reached": math.fsum(weighted_reached)
        / reached_weights
        if reached_weights
        else None,
    }
    for field, expected in expected_optional.items():
        actual = metrics[field]
        if expected is None:
            if actual is not None:
                _fail(f"{label}.{field}: expected null for zero denominator")
        elif actual is None:
            _fail(f"{label}.{field}: missing with nonzero denominator")
        else:
            _close(float(actual), expected, f"{label}.{field}")
    if run["run_status"] == "solver_error":
        if assigned_count or reached_count:
            _fail(f"{label}: solver failure cannot assign/reach requests")
        if not all(value == horizon for value in capped_values):
            _fail(f"{label}: solver failure must cap every response at horizon")


def _metric_summary(values: list[float | None]) -> dict[str, int | float | None]:
    present = [value for value in values if value is not None]
    return {
        "count": len(present),
        "null_count": len(values) - len(present),
        "mean": statistics.fmean(present) if present else None,
        "median": statistics.median(present) if present else None,
        "min": min(present) if present else None,
        "max": max(present) if present else None,
    }


def aggregate_metrics(input_dir: Path, output: Path | None = None) -> dict[str, Any]:
    """Validate a benchmark directory, write summary.json, and return its data.

    Raises ValueError on missing/duplicate/invalid rows, inconsistent raw metrics,
    NaN/Inf, or incomplete paired strategy coverage. Solver errors are data, not
    validation failures, and are reported in the summary.
    """

    manifest, config = _manifest(input_dir / "manifest.json")
    scenarios = _validate_scenarios(_read_jsonl(input_dir / "scenarios.jsonl"), config)
    raw_runs = _read_csv(input_dir / "runs.csv", RUN_REQUIRED_FIELDS)
    raw_requests = _read_csv(input_dir / "requests.csv", REQUEST_REQUIRED_FIELDS)
    strategies = set(config["strategies"])
    expected_runs = {(*key, strategy) for key in scenarios for strategy in strategies}
    runs: dict[RunKey, CsvRow] = {}
    metrics_by_run: dict[RunKey, dict[str, int | float | None]] = {}
    for index, row in enumerate(raw_runs, start=2):
        label = f"runs.csv:{index}"
        key = _run_key(row, label)
        if key not in expected_runs or key in runs:
            _fail(f"{label}: unexpected or duplicate run {key}")
        _validate_identity(row, scenarios[key[:3]], label)
        metrics_by_run[key] = _parse_run_metrics(row, label)
        runs[key] = row
    if set(runs) != expected_runs:
        _fail(f"runs.csv: missing paired strategy runs {sorted(expected_runs - set(runs))}")
    requests_by_run: dict[RunKey, list[CsvRow]] = defaultdict(list)
    for index, row in enumerate(raw_requests, start=2):
        label = f"requests.csv:{index}"
        key = _run_key(row, label)
        if key not in runs:
            _fail(f"{label}: request row has no matching run")
        _validate_identity(row, scenarios[key[:3]], label)
        requests_by_run[key].append(row)
    horizon = config["horizon_seconds"]
    for key, run in runs.items():
        _validate_request_rows(
            key, run, metrics_by_run[key], requests_by_run[key], scenarios[key[:3]], horizon
        )
    groups: dict[tuple[str, str, str], list[RunKey]] = defaultdict(list)
    failures: list[dict[str, Any]] = []
    for key, row in runs.items():
        group_key = (key[2], row["scenario_origin"], key[3])
        groups[group_key].append(key)
        if row["run_status"] == "solver_error":
            failures.append(
                {
                    "seed": key[0],
                    "scenario_id": key[1],
                    "variant": key[2],
                    "strategy": key[3],
                    "error_type": row["error_type"],
                    "error_message": row["error_message"],
                }
            )
    summary_groups: list[dict[str, Any]] = []
    for (variant, origin, strategy), keys in sorted(groups.items()):
        rows = [runs[key] for key in keys]
        summary_groups.append(
            {
                "variant": variant,
                "scenario_origin": origin,
                "strategy": strategy,
                "scenario_count": len(keys),
                "successful_run_count": sum(row["run_status"] == "ok" for row in rows),
                "solver_error_count": sum(row["run_status"] == "solver_error" for row in rows),
                "metrics": {
                    field: _metric_summary(
                        [
                            _present_metric(metrics_by_run[key], field)
                            if metrics_by_run[key][field] is not None
                            else None
                            for key in keys
                        ]
                    )
                    for field in AGGREGATED_METRICS
                },
            }
        )
    summary: dict[str, Any] = {
        "schema_version": "dispatch_aggregate_v1",
        "scenario_variant_count": len(scenarios),
        "strategy_run_count": len(runs),
        "failure_count": len(failures),
        "config": config,
        "validation": {
            "paired_strategy_coverage": True,
            "scenario_records": len(scenarios),
            "run_rows": len(runs),
            "request_rows": len(raw_requests),
            "solver_error_runs": len(failures),
        },
        "failures": sorted(
            failures,
            key=lambda item: (item["seed"], item["scenario_id"], item["variant"], item["strategy"]),
        ),
        "aggregation_unit": "scenario run; request rows are validated but not pooled as replicates",
        "metric_notes": {
            "means": (
                "Unweighted means of per-scenario values; null denominators "
                "are excluded and counted."
            ),
            "reached_only": "Response means condition on reaching the horizon; read with coverage.",
            "capped": "Unreached requests use the declared horizon cap, not an observed arrival.",
            "runtime": (
                "solve_latency_ns measures strategy.solve wall time and varies by machine/run."
            ),
            "failure_convention": (
                "solver_error is retained as a run with no assignments, zero coverage, "
                "and horizon-capped responses for every request. Reached-only means "
                "are null with zero reached requests."
            ),
            "objective": (
                "P/Q/K/C remain in raw runs; they are not averaged across differing scenarios."
            ),
            "controls": (
                "Groups are separated by scenario_origin; controls are not pooled "
                "with generated scenarios."
            ),
        },
        "groups": summary_groups,
    }
    destination = output if output is not None else input_dir / "summary.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        summary = aggregate_metrics(args.input_dir, args.output)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.exit(1, f"aggregation failed: {error}\n")
    print(
        json.dumps(
            {
                "summary": str(args.output or args.input_dir / "summary.json"),
                "scenario_records": summary["validation"]["scenario_records"],
                "run_rows": summary["validation"]["run_rows"],
                "request_rows": summary["validation"]["request_rows"],
                "solver_error_runs": summary["validation"]["solver_error_runs"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
