"""Behavioral checks for the reproducible, offline synthetic SOS benchmark."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path

import pytest
from fixtures_sos import FACT_NAMES
from pydantic import ValidationError

from evaluation.sos_benchmark.generator import generate_dataset, write_jsonl
from evaluation.sos_benchmark.models import BenchmarkRecord
from evaluation.sos_benchmark.summary import summarize_file


def test_same_seed_produces_identical_canonical_jsonl(tmp_path: Path) -> None:
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    changed = tmp_path / "changed.jsonl"

    write_jsonl(generate_dataset(count=100, seed=412073), first)
    write_jsonl(generate_dataset(count=100, seed=412073), second)
    write_jsonl(generate_dataset(count=100, seed=412074), changed)

    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes() != changed.read_bytes()
    assert first.read_bytes().endswith(b"\n")
    assert b"\\u" not in first.read_bytes()  # Vietnamese is serialized as UTF-8.


def test_sample_has_balanced_splits_disjoint_groups_and_visible_strata() -> None:
    records = generate_dataset(count=100, seed=412073)

    assert len(records) == 100
    assert len({record.case_id for record in records}) == 100
    assert Counter(record.split.value for record in records) == {
        "train": 60,
        "dev": 20,
        "test": 20,
    }
    assert Counter(record.generation.signal_bucket.value for record in records) == {
        "lower": 30,
        "intermediate": 35,
        "high": 35,
    }
    assert Counter(record.generation.primary_challenge.value for record in records) == {
        "ordinary": 35,
        "noisy": 25,
        "missing": 15,
        "vague": 15,
        "conflicting": 10,
    }
    assert Counter(record.generation.count_mode.value for record in records) == {
        "exact": 55,
        "range": 10,
        "vague": 15,
        "omitted": 15,
        "conflicting": 5,
    }
    assert Counter(record.generation.injury_mode.value for record in records) == {
        "positive": 30,
        "negative": 20,
        "omitted": 37,
        "hedged": 10,
        "conflicting": 3,
    }
    assert Counter(record.generation.location_mode.value for record in records) == {
        "incident_pin": 40,
        "text_location": 35,
        "reporter_gps": 10,
        "absent": 10,
        "competing": 5,
    }

    families: dict[str, set[str]] = defaultdict(set)
    incident_groups: dict[str, set[str]] = defaultdict(set)
    for record in records:
        split = record.split.value
        families[split].add(record.generation.template_family_id)
        incident_groups[split].add(record.generation.incident_group_id)
    for split_a, split_b in (("train", "dev"), ("train", "test"), ("dev", "test")):
        assert families[split_a].isdisjoint(families[split_b])
        assert incident_groups[split_a].isdisjoint(incident_groups[split_b])


def test_jsonl_round_trips_with_complete_gold_and_synthetic_origin(tmp_path: Path) -> None:
    output = tmp_path / "sample.jsonl"
    write_jsonl(generate_dataset(count=30, seed=41), output)
    lines = output.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 30
    for line in lines:
        record = BenchmarkRecord.model_validate_json(line)
        assert BenchmarkRecord.model_validate_json(record.model_dump_json()) == record
        assert record.synthetic is True
        assert record.metadata.origin == "synthetic_template_rule"
        assert record.metadata.sample is True
        assert record.generated_input.image_ref is None
        assert record.ground_truth.gold_annotation.annotation_origin == "render_trace"
        assert record.ground_truth.gold_annotation.human_audit == "pending"
        assert record.qc.human_review == "pending"
        assert set(type(record.ground_truth.gold_annotation.facts).model_fields) == set(FACT_NAMES)


def test_cli_generates_a_schema_valid_jsonl_file(tmp_path: Path) -> None:
    output = tmp_path / "cli.jsonl"

    subprocess.run(
        [
            sys.executable,
            "-m",
            "evaluation.sos_benchmark.generator",
            "--seed",
            "17",
            "--count",
            "7",
            "--output",
            str(output),
        ],
        check=True,
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )

    assert len(output.read_text(encoding="utf-8").splitlines()) == 7
    assert summarize_file(output)["total"] == 7


def test_cli_is_independent_of_python_hash_seed(tmp_path: Path) -> None:
    outputs = [tmp_path / "hash_1.jsonl", tmp_path / "hash_2.jsonl"]
    for hash_seed, output in zip(("1", "87331"), outputs, strict=True):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = hash_seed
        subprocess.run(
            [
                sys.executable,
                "-m",
                "evaluation.sos_benchmark.generator",
                "--seed",
                "412073",
                "--count",
                "25",
                "--output",
                str(output),
            ],
            check=True,
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            capture_output=True,
            text=True,
        )

    assert outputs[0].read_bytes() == outputs[1].read_bytes()


def test_evidence_spans_and_claim_references_match_original_unicode_text() -> None:
    records = generate_dataset(count=100, seed=412073)
    assert any(any(ord(char) > 127 for char in item.generated_input.raw_text) for item in records)

    for record in records:
        raw_text = record.generated_input.raw_text
        evidence = record.ground_truth.gold_annotation.evidence
        evidence_ids = {item.id for item in evidence}
        assert len(evidence_ids) == len(evidence)
        for item in evidence:
            assert item.source_ref == record.generated_input.sos_id
            if item.text_span is not None:
                span = item.text_span
                assert raw_text[span.start : span.end] == span.quote
            else:
                assert item.source_type == "submitted_gps"
        facts = record.ground_truth.gold_annotation.facts
        for field_name in FACT_NAMES:
            field = getattr(facts, field_name)
            for claim in field.claims:
                assert claim.evidence_ids
                assert set(claim.evidence_ids) <= evidence_ids


def test_hidden_latent_injury_is_not_promoted_to_message_gold() -> None:
    records = generate_dataset(count=100, seed=412073)
    examples = [
        item
        for item in records
        if item.ground_truth.latent_scenario.injury_present
        and "injury_reported" in item.ground_truth.communication_plan.omitted_fields
    ]

    assert examples  # The sample exercises the difference between incident and text truth.
    for item in examples:
        field = item.ground_truth.gold_annotation.facts.injury_reported
        assert field.state == "unknown"
        assert field.value is None
        assert field.unknown_reason == "not_mentioned"
        assert not field.claims


def test_explicit_zero_and_false_remain_distinct_from_unknown() -> None:
    records = generate_dataset(count=100, seed=412073)
    zero_counts = [item for item in records if item.ground_truth.latent_scenario.people_count == 0]
    assert len(zero_counts) == 1
    zero = zero_counts[0].ground_truth.gold_annotation.facts.people_count
    assert zero.state == "supported"
    assert zero.value is not None and zero.value.min == zero.value.max == 0
    assert zero.unknown_reason is None
    assert zero.claims

    explicit_no_injury = [
        item.ground_truth.gold_annotation.facts.injury_reported
        for item in records
        if item.generation.injury_mode == "negative"
    ]
    omitted_injury = [
        item.ground_truth.gold_annotation.facts.injury_reported
        for item in records
        if item.generation.injury_mode == "omitted"
    ]
    assert len(explicit_no_injury) == 20
    assert all(field.state == "supported" and field.value is False for field in explicit_no_injury)
    assert len(omitted_injury) == 37
    assert all(field.state == "unknown" and field.value is None for field in omitted_injury)


def test_render_trace_preserves_preplanned_semantic_claims() -> None:
    records = generate_dataset(count=100, seed=412073)

    for record in records:
        plan = record.ground_truth.communication_plan
        facts = record.ground_truth.gold_annotation.facts
        planned = Counter(
            json.dumps(claim.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
            for claim in plan.planned_claims
        )
        gold: Counter[str] = Counter()
        for field_name in FACT_NAMES:
            for claim in getattr(facts, field_name).claims:
                payload = claim.model_dump(mode="json")
                payload.pop("evidence_ids")
                payload["field_path"] = field_name
                gold[json.dumps(payload, ensure_ascii=False, sort_keys=True)] += 1
        assert planned == gold
        for field in plan.omitted_fields:
            assert getattr(facts, field.value).state == "unknown"
        for field in plan.hedged_fields:
            assert getattr(facts, field.value).state == "uncertain"
        for field in plan.conflict_fields:
            assert getattr(facts, field.value).state == "conflicting"


def test_latent_location_and_time_exist_before_reported_location() -> None:
    records = generate_dataset(count=100, seed=412073)
    for record in records:
        latent = record.ground_truth.latent_scenario
        assert latent.location_role == "incident"
        assert latent.incident_time <= record.generated_input.received_at
        if record.generation.location_mode == "incident_pin":
            submitted = record.generated_input.submitted_location
            assert submitted is not None
            assert submitted.point == latent.incident_point
        elif record.generation.location_mode == "text_location":
            location = record.ground_truth.gold_annotation.facts.incident_location.value
            assert location is not None
            assert location.description == latent.incident_landmark


def test_generator_runs_without_network_access(monkeypatch: pytest.MonkeyPatch) -> None:
    def block_socket(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("offline generator attempted network access")

    monkeypatch.setattr(socket, "socket", block_socket)
    assert len(generate_dataset(count=10, seed=412073)) == 10


def test_primary_challenge_and_hazard_targets_match_visible_gold() -> None:
    records = generate_dataset(count=100, seed=412073)
    assert all(
        not any(
            field.state == "conflicting"
            for field in vars(item.ground_truth.gold_annotation.facts).values()
        )
        for item in records
        if item.generation.primary_challenge == "ordinary"
    )
    assert all(
        item.generation.primary_challenge == "vague"
        for item in records
        if item.generation.count_mode == "vague"
    )
    for field_name, expected in (
        ("water_signals", 55),
        ("fire_signals", 8),
        ("structure_signals", 12),
    ):
        assert (
            sum(
                getattr(item.ground_truth.gold_annotation.facts, field_name).state == "supported"
                for item in records
            )
            == expected
        )


def test_conflicting_count_uses_distinct_same_scope_witness_claims() -> None:
    records = generate_dataset(count=100, seed=412073)
    conflicting = [item for item in records if item.generation.count_mode == "conflicting"]

    assert len(conflicting) == 5
    for item in conflicting:
        field = item.ground_truth.gold_annotation.facts.people_count
        assert field.state == "conflicting"
        assert field.value is None
        assert len(field.claims) >= 2
        first, second = field.claims[:2]
        assert first.witness_id != second.witness_id
        assert first.scope_id == second.scope_id
        assert first.time_ref == second.time_ref


def test_reporter_gps_never_becomes_an_incident_location() -> None:
    records = generate_dataset(count=100, seed=412073)
    reporter_only = [item for item in records if item.generation.location_mode == "reporter_gps"]

    assert len(reporter_only) == 10
    for item in reporter_only:
        submitted = item.generated_input.submitted_location
        assert submitted is not None
        assert submitted.role == "reporter"
        field = item.ground_truth.gold_annotation.facts.incident_location
        assert field.state == "unknown"
        assert field.value is None


def test_vague_and_omitted_count_do_not_gain_invented_numbers() -> None:
    records = generate_dataset(count=100, seed=412073)
    vague = [item for item in records if item.generation.count_mode == "vague"]
    omitted = [item for item in records if item.generation.count_mode == "omitted"]

    assert len(vague) == 15
    assert len(omitted) == 15
    for item in vague:
        field = item.ground_truth.gold_annotation.facts.people_count
        assert field.state == "uncertain"
        assert field.value is None
        assert field.claims
        assert all(claim.value is None for claim in field.claims)
    for item in omitted:
        field = item.ground_truth.gold_annotation.facts.people_count
        assert field.state == "unknown"
        assert field.value is None
        assert not field.claims


def test_record_rejects_a_corrupted_evidence_span() -> None:
    record = generate_dataset(count=1, seed=412073)[0]
    payload = record.model_dump(mode="json")
    evidence = payload["ground_truth"]["gold_annotation"]["evidence"]
    text_evidence = next(item for item in evidence if item["text_span"] is not None)
    changed = deepcopy(payload)
    changed_item = next(
        item
        for item in changed["ground_truth"]["gold_annotation"]["evidence"]
        if item["id"] == text_evidence["id"]
    )
    changed_item["text_span"]["quote"] = "x" * len(changed_item["text_span"]["quote"])

    with pytest.raises(ValidationError):
        BenchmarkRecord.model_validate(changed)


def test_record_rejects_gold_that_changes_a_preplanned_claim() -> None:
    record = generate_dataset(count=100, seed=412073)[0]
    changed = deepcopy(record.model_dump(mode="json"))
    gold = changed["ground_truth"]["gold_annotation"]["facts"]
    count = gold["people_count"]
    if count["state"] != "supported":
        record = next(
            item
            for item in generate_dataset(count=100, seed=412073)
            if item.ground_truth.gold_annotation.facts.people_count.state == "supported"
        )
        changed = deepcopy(record.model_dump(mode="json"))
        count = changed["ground_truth"]["gold_annotation"]["facts"]["people_count"]
    replacement = {"min": 99, "max": 99}
    count["value"] = replacement
    count["claims"][0]["value"] = replacement

    with pytest.raises(ValidationError, match="pre-render communication plan"):
        BenchmarkRecord.model_validate(changed)


def test_distribution_summary_validates_and_counts_jsonl(tmp_path: Path) -> None:
    output = tmp_path / "sample.jsonl"
    write_jsonl(generate_dataset(count=100, seed=412073), output)

    summary = summarize_file(output)

    assert summary["total"] == 100
    assert {split: summary["per_split"][split]["total"] for split in ("train", "dev", "test")} == {
        "train": 60,
        "dev": 20,
        "test": 20,
    }
    assert summary["overall"]["axes"]["signal_bucket"]["high"]["count"] == 35
    assert summary["overall"]["metadata"]["human_review"]["pending"]["count"] == 100


def test_distribution_summary_reports_invalid_line_number(tmp_path: Path) -> None:
    output = tmp_path / "broken.jsonl"
    write_jsonl(generate_dataset(count=1, seed=1), output)
    with output.open("a", encoding="utf-8", newline="") as target:
        target.write("{broken JSON}\n")

    with pytest.raises(ValueError, match=r"broken\.jsonl:2:"):
        summarize_file(output)


def test_distribution_summary_rejects_a_mislabeled_visible_bucket(tmp_path: Path) -> None:
    record = generate_dataset(count=1, seed=1)[0]
    payload = record.model_dump(mode="json")
    payload["generation"]["signal_bucket"] = (
        "high" if record.generation.signal_bucket != "high" else "lower"
    )
    output = tmp_path / "wrong_bucket.jsonl"
    output.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"wrong_bucket\.jsonl:1:"):
        summarize_file(output)


def test_distribution_summary_rejects_template_family_leakage(tmp_path: Path) -> None:
    records = generate_dataset(count=100, seed=412073)
    payloads = [record.model_dump(mode="json") for record in records]
    train_family = next(
        item["generation"]["template_family_id"] for item in payloads if item["split"] == "train"
    )
    dev_index = next(index for index, item in enumerate(payloads) if item["split"] == "dev")
    payloads[dev_index]["generation"]["template_family_id"] = train_family
    output = tmp_path / "leakage.jsonl"
    output.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in payloads),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as error:
        summarize_file(output)
    assert f"{output}:{dev_index + 1}:" in str(error.value)
    assert "template_family_id" in str(error.value)


@pytest.mark.parametrize("count", [0, -1, True])
def test_generator_rejects_invalid_count(count: int) -> None:
    with pytest.raises((TypeError, ValueError)):
        generate_dataset(count=count, seed=1)
