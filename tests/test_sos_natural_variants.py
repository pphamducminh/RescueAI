"""Safety checks for provider-independent SOS language variation."""

from __future__ import annotations

import json
import socket
from collections.abc import Iterable
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from evaluation.sos_benchmark.models import BenchmarkRecord, FactPath
from evaluation.sos_benchmark.natural_variants import (
    RenderFact,
    RenderSpec,
    SemanticReviewDecision,
    SemanticValidator,
    SemanticVerdict,
    VariantInput,
    VariantResult,
    VariantStatus,
    build_render_spec,
    export_render_specs,
    import_variants,
    load_benchmark_cases,
    validate_variant,
)

SAMPLE_PATH = (
    Path(__file__).resolve().parents[1]
    / "experiments"
    / "sos_benchmark"
    / "sample_100.jsonl"
)


@pytest.fixture(scope="module")
def cases() -> tuple[BenchmarkRecord, ...]:
    return tuple(load_benchmark_cases(SAMPLE_PATH))


def _case(cases: Iterable[BenchmarkRecord], case_id: str) -> BenchmarkRecord:
    return next(case for case in cases if case.case_id == case_id)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _variant_line(
    case: BenchmarkRecord,
    *,
    text: str | None = None,
    source_id: str | None = None,
    variant_id: str | None = None,
) -> dict[str, object]:
    original = case.generated_input.raw_text
    punctuation_variant = original.replace("Mọi ng ơi,", "Mọi ng ơi!", 1)
    if punctuation_variant == original:
        punctuation_variant = original.replace(",", ";", 1)
    assert punctuation_variant != original
    variant: dict[str, object] = {
        "text": punctuation_variant if text is None else text,
        "noise_type": "punctuation",
    }
    if variant_id is not None:
        variant["variant_id"] = variant_id
    batch: dict[str, object] = {
        "case_id": case.case_id,
        "variants": [variant],
    }
    if source_id is not None:
        batch["source_id"] = source_id
    return batch


def _import_payload(
    tmp_path: Path,
    records: Iterable[BenchmarkRecord],
    payloads: list[dict[str, object]],
    *,
    semantic_validator: SemanticValidator | None = None,
) -> tuple[Any, Path]:
    source = tmp_path / "import.jsonl"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("".join(_json(item) + "\n" for item in payloads), encoding="utf-8")
    output = tmp_path / "results"
    summary = import_variants(source, records, output, semantic_validator=semantic_validator)
    return summary, output


def test_sample_one_spec_contains_only_reported_facts(cases: tuple[BenchmarkRecord, ...]) -> None:
    case = _case(cases, "syn-v1-000001")
    spec = build_render_spec(case)
    body = spec.model_dump(mode="json")

    assert set(body) == {
        "case_id",
        "allowed_facts",
        "must_remain_unknown",
        "external_context",
        "allowed_distractors",
        "style",
    }
    assert body["case_id"] == case.case_id
    facts = body["allowed_facts"]
    assert facts["people_count"]["state"] == "supported"
    assert facts["people_count"]["value"] == {"min": 10, "max": 10}
    assert facts["injury_reported"]["value"] is True
    assert facts["water_signals"]["value"] == ["water_rising_reported"]
    assert facts["structure_signals"]["value"] == ["cracks_reported"]
    assert "có 10 người" in facts["people_count"]["claims"][0]["source_quotes"]
    assert "nước đang dâng" in facts["water_signals"]["claims"][0]["source_quotes"]

    omitted = {
        "urgent_signs",
        "vulnerable_groups",
        "trapped",
        "fire_signals",
        "access_observations",
        "requested_assistance",
    }
    assert omitted <= set(body["must_remain_unknown"])
    assert omitted.isdisjoint(facts)
    assert "incident_location" not in facts  # Incident pin is external metadata.
    assert "latent_scenario" not in _json(body)
    assert "generation" not in _json(body)


def test_mutating_hidden_latent_facts_does_not_change_spec(
    cases: tuple[BenchmarkRecord, ...],
) -> None:
    case = _case(cases, "syn-v1-000001")
    changed = deepcopy(case.model_dump(mode="json"))
    latent = changed["ground_truth"]["latent_scenario"]
    latent.update(
        trapped=True,
        fire_active=True,
        access_blocked=True,
        urgent_sign="unconscious_reported",
        vulnerable_group="child",
        incident_landmark="điểm bí mật chỉ có trong sự kiện ẩn",
    )
    changed_case = BenchmarkRecord.model_validate(changed)

    original_spec = build_render_spec(case)
    changed_spec = build_render_spec(changed_case)
    assert original_spec.model_dump(mode="json") == changed_spec.model_dump(mode="json")
    assert original_spec.provider_payload() == changed_spec.provider_payload()
    assert "điểm bí mật" not in _json(changed_spec.model_dump(mode="json"))


def test_incident_pin_and_reporter_gps_stay_outside_provider_payload(
    cases: tuple[BenchmarkRecord, ...],
) -> None:
    incident = _case(cases, "syn-v1-000001")
    reporter = _case(cases, "syn-v1-000016")

    for case in (incident, reporter):
        spec = build_render_spec(case)
        point = case.generated_input.submitted_location
        assert point is not None
        assert "submitted_location" in _json(spec.model_dump(mode="json")["external_context"])
        provider = _json(spec.provider_payload())
        assert "external_context" not in provider
        assert str(point.point.lat) not in provider
        assert str(point.point.lon) not in provider
        assert "incident_location" not in spec.allowed_facts

    incident_spec = build_render_spec(incident)
    reporter_spec = build_render_spec(reporter)
    assert "incident_location" not in incident_spec.must_remain_unknown
    assert "incident_location" in reporter_spec.must_remain_unknown


def test_unknown_false_and_zero_have_distinct_render_states(
    cases: tuple[BenchmarkRecord, ...],
) -> None:
    unknown_injury = build_render_spec(_case(cases, "syn-v1-000005"))
    negative_injury = build_render_spec(_case(cases, "syn-v1-000010"))
    zero_count = build_render_spec(_case(cases, "syn-v1-000013"))

    assert "injury_reported" in unknown_injury.must_remain_unknown
    assert "injury_reported" not in unknown_injury.allowed_facts
    assert negative_injury.allowed_facts[FactPath.INJURY_REPORTED].state == "supported"
    assert negative_injury.allowed_facts[FactPath.INJURY_REPORTED].value is False
    assert zero_count.allowed_facts[FactPath.PEOPLE_COUNT].state == "supported"
    assert zero_count.allowed_facts[FactPath.PEOPLE_COUNT].model_dump(mode="json")["value"] == {
        "min": 0,
        "max": 0,
    }


def test_conflicts_and_hedges_keep_claim_semantics(cases: tuple[BenchmarkRecord, ...]) -> None:
    conflict = build_render_spec(_case(cases, "syn-v1-000003"))
    count = conflict.allowed_facts[FactPath.PEOPLE_COUNT].model_dump(mode="json")
    location = conflict.allowed_facts[FactPath.INCIDENT_LOCATION].model_dump(mode="json")
    assert count["state"] == "conflicting" and count["value"] is None
    assert {claim["value"]["min"] for claim in count["claims"]} == {20, 24}
    assert {claim["witness_id"] for claim in count["claims"]} == {"witness_a", "witness_b"}
    assert {claim["scope_id"] for claim in count["claims"]} == {"incident"}
    assert {claim["time_ref"] for claim in count["claims"]} == {"current"}
    assert location["state"] == "conflicting"
    assert len(location["claims"]) == 2

    hedged = build_render_spec(_case(cases, "syn-v1-000009"))
    for field in (FactPath.PEOPLE_COUNT, FactPath.INJURY_REPORTED):
        fact = hedged.allowed_facts[field].model_dump(mode="json")
        assert fact["state"] == "uncertain"
        assert fact["value"] is None
        assert fact["claims"][0]["assertion"] == "hedged"
        assert fact["claims"][0]["source_quotes"]


def test_export_is_deterministic_valid_jsonl_and_has_no_private_truth(
    cases: tuple[BenchmarkRecord, ...], tmp_path: Path
) -> None:
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    export_render_specs(cases[:10], first)
    export_render_specs(cases[:10], second)

    assert first.read_bytes() == second.read_bytes()
    lines = first.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 10
    for line in lines:
        spec = RenderSpec.model_validate_json(line)
        assert spec.case_id.startswith("syn-v1-")
        assert "latent_scenario" not in line
        assert "planned_claims" not in line


def test_valid_import_needs_review_without_semantic_validator(
    cases: tuple[BenchmarkRecord, ...], tmp_path: Path
) -> None:
    case = _case(cases, "syn-v1-000001")
    summary, output = _import_payload(tmp_path, (case,), [_variant_line(case)])

    assert summary.accepted == 0
    assert summary.rejected == 0
    assert summary.needs_review == 1
    assert len((output / "generated_raw.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    assert len((output / "needs_review.jsonl").read_text(encoding="utf-8").splitlines()) == 1


class _ApprovingReviewer:
    def review(self, spec: RenderSpec, variant: VariantInput) -> SemanticReviewDecision:
        assert spec.case_id == "syn-v1-000001"
        assert variant.text
        return SemanticReviewDecision(
            verdict=SemanticVerdict.APPROVED,
            reviewer_id="fixture-reviewer",
            rationale="Semantics checked by this test fixture",
        )


class _FailingReviewer:
    def review(self, spec: RenderSpec, variant: VariantInput) -> SemanticReviewDecision:
        assert spec.case_id == "syn-v1-000001"
        assert variant.text
        raise RuntimeError("semantic reviewer unavailable")


def test_valid_import_is_accepted_after_independent_review(
    cases: tuple[BenchmarkRecord, ...], tmp_path: Path
) -> None:
    case = _case(cases, "syn-v1-000001")
    summary, output = _import_payload(
        tmp_path, (case,), [_variant_line(case)], semantic_validator=_ApprovingReviewer()
    )
    assert summary.accepted == 1
    assert summary.rejected == 0
    assert summary.needs_review == 0
    assert len((output / "accepted.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_failed_reimport_preserves_previous_raw_and_classified_files(
    cases: tuple[BenchmarkRecord, ...], tmp_path: Path
) -> None:
    case = _case(cases, "syn-v1-000001")
    output = tmp_path / "results"
    first_input = tmp_path / "first.jsonl"
    first_input.write_text(_json(_variant_line(case)) + "\n", encoding="utf-8")
    first_summary = import_variants(
        first_input, (case,), output, semantic_validator=_ApprovingReviewer()
    )
    assert first_summary.accepted == 1

    filenames = (
        "generated_raw.jsonl",
        "accepted.jsonl",
        "rejected.jsonl",
        "needs_review.jsonl",
    )
    saved = {name: (output / name).read_bytes() for name in filenames}
    assert saved["generated_raw.jsonl"]
    assert saved["accepted.jsonl"]

    alternative = case.generated_input.raw_text.replace("hiện trạng,", "hiện trạng;", 1)
    assert alternative != case.generated_input.raw_text
    second_input = tmp_path / "second.jsonl"
    second_input.write_text(
        _json(_variant_line(case, text=alternative)) + "\n", encoding="utf-8"
    )
    assert second_input.read_bytes() != first_input.read_bytes()

    with pytest.raises(RuntimeError, match="semantic reviewer unavailable"):
        import_variants(second_input, (case,), output, semantic_validator=_FailingReviewer())

    assert {name: (output / name).read_bytes() for name in filenames} == saved


@pytest.mark.parametrize(
    ("replacement", "reason"),
    [
        ("có 11 người", "changed count"),
        ("có người", "removed required count"),
        ("có 10 người, có 27 người", "added count"),
    ],
)
def test_import_rejects_numeric_changes(
    cases: tuple[BenchmarkRecord, ...],
    tmp_path: Path,
    replacement: str,
    reason: str,
) -> None:
    case = _case(cases, "syn-v1-000001")
    changed = case.generated_input.raw_text.replace("có 10 người", replacement, 1)
    assert changed != case.generated_input.raw_text, reason
    summary, _ = _import_payload(tmp_path, (case,), [_variant_line(case, text=changed)])
    assert summary.rejected == 1, reason


def test_nested_vulnerable_group_count_is_required(
    cases: tuple[BenchmarkRecord, ...],
) -> None:
    """Future counted vulnerable groups must retain their reported number."""

    case = _case(cases, "syn-v1-000003")
    source_spec = build_render_spec(case)
    fact_data = source_spec.allowed_facts[FactPath.VULNERABLE_GROUPS].model_dump(mode="json")
    fact_data["value"][0]["count"] = {"min": 2, "max": 2}
    fact_data["claims"][0]["value"][0]["count"] = {"min": 2, "max": 2}
    fact_data["claims"][0]["source_quotes"] = ["có 2 trẻ nhỏ"]
    counted_fact = RenderFact.model_validate(fact_data)
    spec_data = source_spec.model_dump(mode="json")
    spec_data["allowed_facts"]["vulnerable_groups"] = counted_fact.model_dump(mode="json")
    counted_spec = RenderSpec.model_validate(spec_data)

    missing = validate_variant(counted_spec, case.generated_input.raw_text)
    assert "missing_required_numeric:vulnerable_groups:2" in missing.rejection_reasons
    present = validate_variant(counted_spec, case.generated_input.raw_text + " Có 2 trẻ nhỏ.")
    assert "missing_required_numeric:vulnerable_groups:2" not in present.rejection_reasons


def test_count_number_in_unrelated_time_does_not_satisfy_required_count(
    cases: tuple[BenchmarkRecord, ...], tmp_path: Path
) -> None:
    case = _case(cases, "syn-v1-000001")
    changed = case.generated_input.raw_text.replace(
        "có 10 người", "sau 10 phút, có người", 1
    )
    summary, output = _import_payload(tmp_path, (case,), [_variant_line(case, text=changed)])

    assert summary.rejected == 1
    line = (output / "rejected.jsonl").read_text(encoding="utf-8").strip()
    rejected = VariantResult.model_validate_json(line)
    assert "missing_required_count:10" in rejected.reasons


def test_held_out_incident_location_cannot_be_written_into_text(
    cases: tuple[BenchmarkRecord, ...], tmp_path: Path
) -> None:
    case = _case(cases, "syn-v1-000001")
    spec = build_render_spec(case)
    assert "incident_location" in spec.external_context.held_out_facts
    changed = case.generated_input.raw_text + " Nơi xảy ra sự việc gần cây cầu nhỏ."
    summary, output = _import_payload(tmp_path, (case,), [_variant_line(case, text=changed)])

    assert summary.rejected == 1
    line = (output / "rejected.jsonl").read_text(encoding="utf-8").strip()
    rejected = VariantResult.model_validate_json(line)
    assert "held_out_location_cue" in rejected.reasons


@pytest.mark.parametrize(
    "added_claim",
    ["Không có người ở đó.", "Hiện có khoảng mười hai người."],
)
def test_unknown_people_count_rejects_denial_and_written_number(
    cases: tuple[BenchmarkRecord, ...], tmp_path: Path, added_claim: str
) -> None:
    case = _case(cases, "syn-v1-000011")
    assert case.ground_truth.gold_annotation.facts.people_count.state == "unknown"
    changed = case.generated_input.raw_text + " " + added_claim
    summary, output = _import_payload(tmp_path, (case,), [_variant_line(case, text=changed)])

    assert summary.rejected == 1
    line = (output / "rejected.jsonl").read_text(encoding="utf-8").strip()
    rejected = VariantResult.model_validate_json(line)
    assert "unknown_field_cue:people_count" in rejected.reasons


def test_import_rejects_obvious_unknown_leak_and_false_inversion(
    cases: tuple[BenchmarkRecord, ...], tmp_path: Path
) -> None:
    unknown_case = _case(cases, "syn-v1-000005")
    unknown_text = unknown_case.generated_input.raw_text + " Không ai bị thương."
    unknown_summary, _ = _import_payload(
        tmp_path / "unknown", (unknown_case,), [_variant_line(unknown_case, text=unknown_text)]
    )
    assert unknown_summary.rejected == 1

    false_case = _case(cases, "syn-v1-000010")
    false_text = false_case.generated_input.raw_text + " Có người bị thương."
    false_summary, _ = _import_payload(
        tmp_path / "false", (false_case,), [_variant_line(false_case, text=false_text)]
    )
    assert false_summary.rejected == 1


def test_import_rejects_external_gps_coordinates_in_text(
    cases: tuple[BenchmarkRecord, ...], tmp_path: Path
) -> None:
    case = _case(cases, "syn-v1-000001")
    submitted = case.generated_input.submitted_location
    assert submitted is not None
    with_gps = f"{case.generated_input.raw_text} {submitted.point.lat}, {submitted.point.lon}."
    summary, _ = _import_payload(tmp_path, (case,), [_variant_line(case, text=with_gps)])
    assert summary.rejected == 1


def test_import_rejects_controlled_unauthorized_distractor(
    cases: tuple[BenchmarkRecord, ...], tmp_path: Path
) -> None:
    case = _case(cases, "syn-v1-000002")
    assert "irrelevant" not in case.ground_truth.communication_plan.noise_edits
    with_distractor = case.generated_input.raw_text + " Điện thoại tôi hỏng màn hình."
    summary, output = _import_payload(
        tmp_path, (case,), [_variant_line(case, text=with_distractor)]
    )
    assert summary.rejected == 1
    assert "unauthorized_distractor" in (output / "rejected.jsonl").read_text(encoding="utf-8")


def test_import_rejects_blank_duplicate_and_unknown_case(
    cases: tuple[BenchmarkRecord, ...], tmp_path: Path
) -> None:
    case = _case(cases, "syn-v1-000001")
    valid = _variant_line(case)
    duplicate = deepcopy(valid)
    variants = valid["variants"]
    assert isinstance(variants, list)
    duplicate["variants"] = variants + variants
    summary, _ = _import_payload(tmp_path / "duplicate", (case,), [duplicate])
    assert summary.rejected >= 1

    blank: dict[str, object] = {
        "case_id": case.case_id,
        "variants": [{"text": "  ", "noise_type": "punctuation"}],
    }
    summary, _ = _import_payload(tmp_path / "blank", (case,), [blank])
    assert summary.rejected == 1

    unknown = _variant_line(case)
    unknown["case_id"] = "syn-v1-999999"
    summary, _ = _import_payload(tmp_path / "case_id", (case,), [unknown])
    assert summary.rejected == 1


def test_import_rejects_invalid_schema_and_duplicate_source_and_variant_ids(
    cases: tuple[BenchmarkRecord, ...], tmp_path: Path
) -> None:
    case = _case(cases, "syn-v1-000001")
    bad_schema: dict[str, object] = {
        "case_id": case.case_id,
        "variants": [{"text": 42, "noise_type": "punctuation"}],
    }
    summary, _ = _import_payload(tmp_path / "schema", (case,), [bad_schema])
    assert summary.rejected == 1

    alternative = case.generated_input.raw_text.replace("hiện trạng,", "hiện trạng;", 1)
    assert alternative != case.generated_input.raw_text
    repeated_source = [
        _variant_line(case, source_id="batch-1", variant_id="variant-a"),
        _variant_line(case, text=alternative, source_id="batch-1", variant_id="variant-b"),
    ]
    summary, output = _import_payload(tmp_path / "repeated-source", (case,), repeated_source)
    assert summary.rejected == 1
    assert "duplicate_source_id" in (output / "rejected.jsonl").read_text(encoding="utf-8")

    repeated_variant = [
        _variant_line(case, source_id="batch-1", variant_id="variant-a"),
        _variant_line(case, text=alternative, source_id="batch-2", variant_id="variant-a"),
    ]
    summary, output = _import_payload(tmp_path / "repeated-variant", (case,), repeated_variant)
    assert summary.rejected == 1
    assert "duplicate_variant_id" in (output / "rejected.jsonl").read_text(encoding="utf-8")


def test_malformed_jsonl_is_preserved_and_classified_as_rejected(
    cases: tuple[BenchmarkRecord, ...], tmp_path: Path
) -> None:
    case = _case(cases, "syn-v1-000001")
    source = tmp_path / "malformed.jsonl"
    raw = b'{"case_id": "syn-v1-000001", "variants": [}\n'
    source.write_bytes(raw)
    output = tmp_path / "results"

    summary = import_variants(source, (case,), output)

    assert summary.rejected == 1
    assert (output / "generated_raw.jsonl").read_bytes() == raw
    line = (output / "rejected.jsonl").read_text(encoding="utf-8").strip()
    rejected = VariantResult.model_validate_json(line)
    assert rejected.status is VariantStatus.REJECTED
    assert rejected.reasons == ("invalid_batch_json_or_schema",)


def test_import_output_is_deterministic_and_fully_offline(
    cases: tuple[BenchmarkRecord, ...], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _case(cases, "syn-v1-000001")

    def block_socket(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("variation pipeline attempted network access")

    monkeypatch.setattr(socket, "socket", block_socket)
    _, first = _import_payload(tmp_path / "first", (case,), [_variant_line(case)])
    _, second = _import_payload(tmp_path / "second", (case,), [_variant_line(case)])
    for name in ("generated_raw.jsonl", "accepted.jsonl", "rejected.jsonl", "needs_review.jsonl"):
        assert (first / name).read_bytes() == (second / name).read_bytes()
