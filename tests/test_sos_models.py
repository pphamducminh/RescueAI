"""Contract tests for synthetic SOS snapshots; no extraction service is exercised."""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from typing import Any, TypeVar

import pytest
from fixtures_sos import (
    FACT_NAMES,
    ambiguous_case,
    conflicting_case,
    critical_case,
    ordinary_case,
    reviewed_ordinary_case,
    synthetic_ai_image_case,
    synthetic_ai_text_case,
    unassessed_priority_case,
)
from pydantic import BaseModel, ValidationError

from backend.domain.sos import (
    EvidenceField,
    ExtractedSOS,
    PriorityAssessment,
    SOSInput,
    ValidatedSOS,
    WaterTag,
    validate_extraction_against_input,
)

ModelT = TypeVar("ModelT", bound=BaseModel)
CaseFactory = Callable[[], tuple[dict[str, Any], dict[str, Any]]]


def _parse_json(model_type: type[ModelT], payload: dict[str, Any]) -> ModelT:
    return model_type.model_validate_json(json.dumps(payload, ensure_ascii=False))


@pytest.mark.parametrize(
    "case_factory", [ordinary_case, critical_case, ambiguous_case, conflicting_case]
)
def test_synthetic_cases_round_trip_and_preserve_source(
    case_factory: CaseFactory,
) -> None:
    source_data, extraction_data = case_factory()
    source = _parse_json(SOSInput, source_data)
    extraction = _parse_json(ExtractedSOS, extraction_data)

    assert SOSInput.model_validate_json(source.model_dump_json()) == source
    assert ExtractedSOS.model_validate_json(extraction.model_dump_json()) == extraction
    assert source.raw_text == source_data["raw_text"]
    assert set(type(extraction.facts).model_fields) == set(FACT_NAMES)
    validate_extraction_against_input(extraction, source)


def test_review_and_priority_snapshots_round_trip() -> None:
    review = _parse_json(ValidatedSOS, reviewed_ordinary_case())
    assessment = _parse_json(PriorityAssessment, unassessed_priority_case())

    assert ValidatedSOS.model_validate_json(review.model_dump_json()) == review
    assert PriorityAssessment.model_validate_json(assessment.model_dump_json()) == assessment
    assert set(type(review.fields).model_fields) == set(FACT_NAMES)
    assert review.location_resolution.routing_anchor is not None
    assert assessment.priority_weight is None
    assert assessment.requires_human_review is True


def test_json_compatible_dicts_deserialize_for_all_four_snapshots() -> None:
    source_data, extraction_data = ordinary_case()
    source = SOSInput.model_validate(source_data)
    extraction = ExtractedSOS.model_validate(extraction_data)
    review = ValidatedSOS.model_validate(reviewed_ordinary_case())
    assessment = PriorityAssessment.model_validate(unassessed_priority_case())

    assert SOSInput.model_validate(source.model_dump(mode="json")) == source
    assert ExtractedSOS.model_validate(extraction.model_dump(mode="json")) == extraction
    assert ValidatedSOS.model_validate(review.model_dump(mode="json")) == review
    assert PriorityAssessment.model_validate(assessment.model_dump(mode="json")) == assessment


@pytest.mark.parametrize("confidence", [0.0, 0.5, 1.0])
def test_synthetic_ai_text_provenance_round_trips_with_valid_confidence(
    confidence: float,
) -> None:
    source_data, extraction_data = synthetic_ai_text_case(confidence)
    source = _parse_json(SOSInput, source_data)
    extraction = _parse_json(ExtractedSOS, extraction_data)

    assert ExtractedSOS.model_validate_json(extraction.model_dump_json()) == extraction
    count = extraction.facts.people_count
    assert count.confidence == confidence
    assert count.candidates[0].confidence == confidence
    assert count.candidates[0].method == "ai_text"
    validate_extraction_against_input(extraction, source)


def test_synthetic_image_observation_preserves_region_and_source_reference() -> None:
    source_data, extraction_data = synthetic_ai_image_case()
    source = _parse_json(SOSInput, source_data)
    extraction = _parse_json(ExtractedSOS, extraction_data)

    smoke = extraction.facts.fire_signals
    assert smoke.value == ("smoke_observed",)
    assert smoke.candidates[0].method == "ai_image"
    image_evidence = next(item for item in extraction.evidence if item.id == "smoke_region")
    assert image_evidence.source_ref == source.image_ref
    assert image_evidence.image_box is not None
    validate_extraction_against_input(extraction, source)


def test_ordinary_fixture_retains_explicit_negative_claims_and_location_role() -> None:
    source_data, extraction_data = ordinary_case()
    source = _parse_json(SOSInput, source_data)
    extraction = _parse_json(ExtractedSOS, extraction_data)

    assert extraction.facts.injury_reported.state == "supported"
    assert extraction.facts.injury_reported.value is False
    assert extraction.facts.trapped.value is False
    assert extraction.facts.urgent_signs.state == "unknown"
    assert source.submitted_location is not None
    assert source.submitted_location.role == "incident"
    assert extraction.facts.incident_location.state == "supported"


def test_critical_fixture_keeps_missing_location_and_injury_unknown() -> None:
    _, extraction_data = critical_case()
    extraction = _parse_json(ExtractedSOS, extraction_data)

    assert extraction.facts.urgent_signs.state == "supported"
    assert extraction.facts.trapped.value is True
    assert extraction.facts.injury_reported.state == "unknown"
    assert extraction.facts.incident_location.state == "unknown"


def test_ambiguous_fixture_keeps_hedged_claims_and_reporter_gps_separate() -> None:
    source_data, extraction_data = ambiguous_case()
    source = _parse_json(SOSInput, source_data)
    extraction = _parse_json(ExtractedSOS, extraction_data)

    assert source.submitted_location is not None
    assert source.submitted_location.role == "reporter"
    assert extraction.facts.people_count.state == "uncertain"
    assert extraction.facts.people_count.value is None
    assert extraction.facts.people_count.candidates[0].value is None
    assert extraction.facts.urgent_signs.candidates[0].assertion == "hedged"
    assert extraction.facts.incident_location.state == "unknown"


def test_conflicting_fixture_preserves_both_evidence_backed_claims() -> None:
    _, extraction_data = conflicting_case()
    extraction = _parse_json(ExtractedSOS, extraction_data)

    assert extraction.facts.people_count.state == "conflicting"
    assert extraction.facts.people_count.value is None
    assert len(extraction.facts.people_count.candidates) == 2
    assert {c.evidence_ids[0] for c in extraction.facts.people_count.candidates} == {
        "count_two",
        "count_five",
    }
    assert extraction.facts.fire_signals.state == "conflicting"
    assert len(extraction.facts.fire_signals.candidates) == 2


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        ("input_revision", 0),
        ("raw_text", ""),
        ("received_at", "2026-09-28T08:00:00"),
        ("received_at", "1660000000"),
        ("received_at", "2026-09-28T15:00:00+07:00"),
        ("schema_version", "2.0"),
    ],
)
def test_input_rejects_invalid_required_values(field_name: str, bad_value: Any) -> None:
    source_data, _ = ordinary_case()
    source_data[field_name] = bad_value
    with pytest.raises(ValidationError):
        _parse_json(SOSInput, source_data)


@pytest.mark.parametrize("missing_key", ["image_ref", "submitted_location", "reported_event_at"])
def test_input_requires_explicit_nullable_keys(missing_key: str) -> None:
    source_data, _ = ordinary_case()
    source_data.pop(missing_key)
    with pytest.raises(ValidationError):
        _parse_json(SOSInput, source_data)


def test_input_rejects_unrecognized_extra_field() -> None:
    source_data, _ = ordinary_case()
    source_data["dispatch_priority"] = "critical"
    with pytest.raises(ValidationError):
        _parse_json(SOSInput, source_data)


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [("input_revision", "1"), ("input_revision", True), ("received_at", 1234)],
)
def test_input_rejects_primitive_coercion(field_name: str, bad_value: Any) -> None:
    source_data, _ = ordinary_case()
    source_data[field_name] = bad_value
    with pytest.raises(ValidationError):
        SOSInput.model_validate(source_data)


@pytest.mark.parametrize(
    ("coordinate", "bad_value"),
    [("lat", -90.01), ("lat", 90.01), ("lon", -180.01), ("lon", 180.01)],
)
def test_input_rejects_out_of_range_coordinates(coordinate: str, bad_value: float) -> None:
    source_data, _ = ordinary_case()
    source_data["submitted_location"]["point"][coordinate] = bad_value
    with pytest.raises(ValidationError):
        _parse_json(SOSInput, source_data)


def test_input_rejects_unknown_location_role() -> None:
    source_data, _ = ordinary_case()
    source_data["submitted_location"]["role"] = "victim"
    with pytest.raises(ValidationError):
        _parse_json(SOSInput, source_data)


def test_input_does_not_coerce_revision_or_accuracy_from_strings() -> None:
    source_data, _ = ordinary_case()
    source_data["input_revision"] = "1"
    with pytest.raises(ValidationError):
        _parse_json(SOSInput, source_data)

    source_data, _ = ordinary_case()
    source_data["submitted_location"]["accuracy_m"] = "10"
    with pytest.raises(ValidationError):
        _parse_json(SOSInput, source_data)


@pytest.mark.parametrize("missing_fact", FACT_NAMES)
def test_extraction_requires_every_fact_even_when_unknown(missing_fact: str) -> None:
    _, extraction_data = ordinary_case()
    extraction_data["facts"].pop(missing_fact)
    with pytest.raises(ValidationError):
        _parse_json(ExtractedSOS, extraction_data)


@pytest.mark.parametrize(
    "bad_field",
    [
        {
            "state": "supported",
            "value": None,
            "candidates": [],
            "confidence": None,
            "unknown_reason": None,
        },
        {
            "state": "supported",
            "value": False,
            "candidates": [],
            "confidence": None,
            "unknown_reason": None,
        },
        {
            "state": "unknown",
            "value": False,
            "candidates": [],
            "confidence": None,
            "unknown_reason": "not_mentioned",
        },
        {
            "state": "unknown",
            "value": None,
            "candidates": [],
            "confidence": None,
            "unknown_reason": None,
        },
        {
            "state": "uncertain",
            "value": None,
            "candidates": [],
            "confidence": None,
            "unknown_reason": None,
        },
        {
            "state": "conflicting",
            "value": None,
            "candidates": [],
            "confidence": None,
            "unknown_reason": None,
        },
    ],
    ids=[
        "supported-without-value",
        "supported-without-evidence",
        "unknown-with-value",
        "unknown-without-reason",
        "uncertain-without-candidate",
        "conflict-without-two-candidates",
    ],
)
def test_extraction_rejects_invalid_field_states(bad_field: dict[str, Any]) -> None:
    _, extraction_data = ordinary_case()
    extraction_data["facts"]["injury_reported"] = bad_field
    with pytest.raises(ValidationError):
        _parse_json(ExtractedSOS, extraction_data)


@pytest.mark.parametrize("bad_confidence", [-0.01, 1.01])
def test_extraction_rejects_out_of_range_field_confidence(bad_confidence: float) -> None:
    _, extraction_data = ordinary_case()
    extraction_data["facts"]["injury_reported"]["confidence"] = bad_confidence
    with pytest.raises(ValidationError):
        _parse_json(ExtractedSOS, extraction_data)


@pytest.mark.parametrize("bad_confidence", [-0.01, 1.01])
def test_extraction_rejects_out_of_range_candidate_confidence(bad_confidence: float) -> None:
    _, extraction_data = ordinary_case()
    extraction_data["facts"]["injury_reported"]["candidates"][0]["confidence"] = bad_confidence
    with pytest.raises(ValidationError):
        _parse_json(ExtractedSOS, extraction_data)


def test_extraction_rejects_confidence_for_uncertain_field() -> None:
    _, extraction_data = ambiguous_case()
    extraction_data["facts"]["people_count"]["confidence"] = 0.8
    with pytest.raises(ValidationError):
        _parse_json(ExtractedSOS, extraction_data)


def test_extraction_rejects_coerced_bool_and_confidence() -> None:
    _, extraction_data = ordinary_case()
    extraction_data["facts"]["injury_reported"]["value"] = "false"
    extraction_data["facts"]["injury_reported"]["candidates"][0]["value"] = "false"
    with pytest.raises(ValidationError):
        ExtractedSOS.model_validate(extraction_data)

    _, extraction_data = ordinary_case()
    extraction_data["facts"]["injury_reported"]["confidence"] = "0.8"
    with pytest.raises(ValidationError):
        ExtractedSOS.model_validate(extraction_data)


def test_reordered_identical_tags_are_not_a_conflict() -> None:
    _, extraction_data = critical_case()
    field = extraction_data["facts"]["water_signals"]
    field["state"] = "conflicting"
    field["value"] = None
    field["candidates"] = [
        {
            **field["candidates"][0],
            "value": ["flooding_reported", "water_rising_reported"],
        },
        {
            **field["candidates"][0],
            "value": ["water_rising_reported", "flooding_reported"],
        },
    ]
    with pytest.raises(ValidationError):
        _parse_json(ExtractedSOS, extraction_data)


def test_compatible_positive_tags_can_form_one_supported_field() -> None:
    field_data = {
        "state": "supported",
        "value": ["flooding_reported", "water_rising_reported"],
        "candidates": [
            {
                "value": [tag],
                "assertion": "stated",
                "method": "ai_text",
                "evidence_ids": [evidence_id],
                "confidence": None,
            }
            for tag, evidence_id in (
                ("flooding_reported", "flood"),
                ("water_rising_reported", "rising"),
            )
        ],
        "confidence": None,
        "unknown_reason": None,
    }

    field = EvidenceField[tuple[WaterTag, ...]].model_validate_json(json.dumps(field_data))
    assert set(field.value or ()) == {
        WaterTag.FLOODING_REPORTED,
        WaterTag.WATER_RISING_REPORTED,
    }

    field_data["state"] = "conflicting"
    field_data["value"] = None
    with pytest.raises(ValidationError):
        EvidenceField[tuple[WaterTag, ...]].model_validate_json(json.dumps(field_data))


def test_overlapping_count_ranges_are_not_a_structural_conflict() -> None:
    _, extraction_data = conflicting_case()
    candidates = extraction_data["facts"]["people_count"]["candidates"]
    candidates[0]["value"] = {"min": 2, "max": 5}
    candidates[1]["value"] = {"min": 4, "max": 7}
    with pytest.raises(ValidationError):
        _parse_json(ExtractedSOS, extraction_data)


def test_image_only_denial_is_invalid_even_inside_conflicting_field() -> None:
    _, extraction_data = conflicting_case()
    extraction_data["extractor"]["kind"] = "ai"
    extraction_data["extractor"]["model_id"] = "synthetic-test-model"
    extraction_data["extractor"]["modalities_used"] = ["text", "image"]
    extraction_data["evidence"].append(
        {
            "id": "image_no_fire",
            "source_type": "sos_image",
            "source_ref": "synthetic-image",
            "text_span": None,
            "image_box": {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2},
            "captured_at": None,
        }
    )
    denial = extraction_data["facts"]["fire_signals"]["candidates"][1]
    denial["method"] = "ai_image"
    denial["evidence_ids"] = ["image_no_fire"]
    with pytest.raises(ValidationError):
        _parse_json(ExtractedSOS, extraction_data)


@pytest.mark.parametrize("bad_range", [{"min": -1, "max": 2}, {"min": 5, "max": 2}])
def test_extraction_rejects_invalid_people_range(bad_range: dict[str, int]) -> None:
    _, extraction_data = ordinary_case()
    count = extraction_data["facts"]["people_count"]
    count["value"] = bad_range
    count["candidates"][0]["value"] = bad_range
    with pytest.raises(ValidationError):
        _parse_json(ExtractedSOS, extraction_data)


def test_extraction_rejects_unsupported_tag() -> None:
    _, extraction_data = critical_case()
    field = extraction_data["facts"]["urgent_signs"]
    field["value"] = ["diagnosed_heart_attack"]
    field["candidates"][0]["value"] = ["diagnosed_heart_attack"]
    with pytest.raises(ValidationError):
        _parse_json(ExtractedSOS, extraction_data)


def test_extraction_does_not_coerce_a_string_into_a_boolean_claim() -> None:
    _, extraction_data = ordinary_case()
    field = extraction_data["facts"]["injury_reported"]
    field["value"] = "false"
    field["candidates"][0]["value"] = "false"
    with pytest.raises(ValidationError):
        _parse_json(ExtractedSOS, extraction_data)


def test_extraction_rejects_missing_candidate_evidence_id() -> None:
    _, extraction_data = ordinary_case()
    extraction_data["facts"]["people_count"]["candidates"][0]["evidence_ids"] = ["missing"]
    with pytest.raises(ValidationError):
        _parse_json(ExtractedSOS, extraction_data)


def test_extraction_rejects_duplicate_evidence_ids() -> None:
    _, extraction_data = ordinary_case()
    extraction_data["evidence"].append(deepcopy(extraction_data["evidence"][0]))
    with pytest.raises(ValidationError):
        _parse_json(ExtractedSOS, extraction_data)


def test_extraction_rejects_image_region_outside_normalized_bounds() -> None:
    _, extraction_data = synthetic_ai_image_case()
    extraction_data["evidence"][-1]["image_box"]["x"] = 0.8
    extraction_data["evidence"][-1]["image_box"]["w"] = 0.3
    with pytest.raises(ValidationError):
        _parse_json(ExtractedSOS, extraction_data)


@pytest.mark.parametrize(
    ("precision", "point"),
    [("point", None), ("landmark", {"lat": 10.8, "lon": 106.7})],
)
def test_extraction_rejects_location_precision_mismatch(
    precision: str, point: dict[str, float] | None
) -> None:
    _, extraction_data = ordinary_case()
    location = {"description": "synthetic location", "point": point, "precision": precision}
    field = extraction_data["facts"]["incident_location"]
    field["value"] = location
    field["candidates"][0]["value"] = location
    with pytest.raises(ValidationError):
        _parse_json(ExtractedSOS, extraction_data)


def test_review_requires_every_fact() -> None:
    review_data = reviewed_ordinary_case()
    review_data["fields"].pop("trapped")
    with pytest.raises(ValidationError):
        _parse_json(ValidatedSOS, review_data)


def test_complete_review_cannot_contain_not_reviewed_field() -> None:
    review_data = reviewed_ordinary_case()
    review_data["overall_review_state"] = "complete"
    with pytest.raises(ValidationError):
        _parse_json(ValidatedSOS, review_data)


@pytest.mark.parametrize("missing_key", ["reviewer_id", "reviewed_at", "evidence_ids"])
def test_accepted_field_requires_reviewer_time_and_evidence(missing_key: str) -> None:
    review_data = reviewed_ordinary_case()
    accepted_count = review_data["fields"]["people_count"]
    accepted_count[missing_key] = [] if missing_key == "evidence_ids" else None
    with pytest.raises(ValidationError):
        _parse_json(ValidatedSOS, review_data)


def test_not_reviewed_field_cannot_carry_a_value() -> None:
    review_data = reviewed_ordinary_case()
    review_data["fields"]["trapped"]["value"] = False
    with pytest.raises(ValidationError):
        _parse_json(ValidatedSOS, review_data)


def test_routing_anchor_requires_accepted_incident_point() -> None:
    review_data = reviewed_ordinary_case()
    review_data["fields"]["incident_location"] = {
        "review_state": "unresolved",
        "value": None,
        "evidence_ids": [],
        "reviewer_id": None,
        "reviewed_at": None,
        "reason": None,
    }
    with pytest.raises(ValidationError):
        _parse_json(ValidatedSOS, review_data)


def test_reported_point_requires_review_even_without_a_routing_anchor() -> None:
    review_data = reviewed_ordinary_case()
    review_data["location_resolution"]["routing_anchor"] = None
    review_data["fields"]["incident_location"] = {
        "review_state": "not_reviewed",
        "value": None,
        "evidence_ids": [],
        "reviewer_id": None,
        "reviewed_at": None,
        "reason": None,
    }
    with pytest.raises(ValidationError):
        _parse_json(ValidatedSOS, review_data)


def test_externally_confirmed_point_requires_external_location_review() -> None:
    review_data = reviewed_ordinary_case()
    review_data["location_resolution"]["quality"] = "externally_confirmed_point"
    with pytest.raises(ValidationError):
        _parse_json(ValidatedSOS, review_data)


@pytest.mark.parametrize("weight", [-1.0, 0.0])
def test_priority_rejects_nonpositive_weight(weight: float) -> None:
    assessment_data = unassessed_priority_case()
    assessment_data["priority_weight"] = weight
    with pytest.raises(ValidationError):
        _parse_json(PriorityAssessment, assessment_data)


def test_insufficient_information_cannot_carry_a_weight() -> None:
    assessment_data = unassessed_priority_case()
    assessment_data["priority_weight"] = 1.0
    with pytest.raises(ValidationError):
        _parse_json(PriorityAssessment, assessment_data)


def test_extracted_assessment_always_requests_human_review() -> None:
    assessment_data = unassessed_priority_case()
    assessment_data["requires_human_review"] = False
    with pytest.raises(ValidationError):
        _parse_json(PriorityAssessment, assessment_data)


def test_named_attention_level_requires_explaining_reason() -> None:
    assessment_data = unassessed_priority_case()
    assessment_data["suggested_attention"] = "standard_review"
    with pytest.raises(ValidationError):
        _parse_json(PriorityAssessment, assessment_data)


@pytest.mark.parametrize(
    "case_factory", [ordinary_case, critical_case, ambiguous_case, conflicting_case]
)
def test_all_text_evidence_spans_match_unchanged_original(case_factory: CaseFactory) -> None:
    source_data, extraction_data = case_factory()
    for evidence in extraction_data["evidence"]:
        if evidence["source_type"] != "sos_text":
            continue
        span = evidence["text_span"]
        assert source_data["raw_text"][span["start"] : span["end"]] == span["quote"]


@pytest.mark.parametrize("changed_key", ["sos_id", "input_revision"])
def test_paired_validation_rejects_snapshot_mismatch(changed_key: str) -> None:
    source_data, extraction_data = ordinary_case()
    extraction_data[changed_key] = "another-sos" if changed_key == "sos_id" else 2
    source = _parse_json(SOSInput, source_data)
    extraction = _parse_json(ExtractedSOS, extraction_data)
    with pytest.raises(ValueError):
        validate_extraction_against_input(extraction, source)


def test_paired_validation_rejects_text_quote_mismatch() -> None:
    source_data, extraction_data = ordinary_case()
    extraction_data["evidence"][0]["text_span"]["quote"] = "3 người"
    source = _parse_json(SOSInput, source_data)
    extraction = _parse_json(ExtractedSOS, extraction_data)
    with pytest.raises(ValueError):
        validate_extraction_against_input(extraction, source)


@pytest.mark.parametrize("role", ["reporter", "unspecified"])
def test_paired_validation_rejects_nonincident_gps_as_incident_location(role: str) -> None:
    source_data, extraction_data = ordinary_case()
    source_data["submitted_location"]["role"] = role
    source = _parse_json(SOSInput, source_data)
    extraction = _parse_json(ExtractedSOS, extraction_data)
    with pytest.raises(ValueError):
        validate_extraction_against_input(extraction, source)


def test_paired_validation_rejects_image_evidence_without_submitted_image() -> None:
    source_data, extraction_data = synthetic_ai_image_case()
    source_data["image_ref"] = None
    source = _parse_json(SOSInput, source_data)
    extraction = _parse_json(ExtractedSOS, extraction_data)
    with pytest.raises(ValueError):
        validate_extraction_against_input(extraction, source)


def test_review_reference_matches_its_extraction() -> None:
    _, extraction_data = ordinary_case()
    review = _parse_json(ValidatedSOS, reviewed_ordinary_case())
    extraction = _parse_json(ExtractedSOS, extraction_data)
    review.assert_matches_extraction(extraction)


def test_review_reference_rejects_a_different_extraction() -> None:
    _, extraction_data = critical_case()
    review = _parse_json(ValidatedSOS, reviewed_ordinary_case())
    extraction = _parse_json(ExtractedSOS, extraction_data)
    with pytest.raises(ValueError):
        review.assert_matches_extraction(extraction)


def test_review_reference_rejects_missing_evidence_id() -> None:
    _, extraction_data = ordinary_case()
    review_data = reviewed_ordinary_case()
    review_data["fields"]["people_count"]["evidence_ids"] = ["nonexistent"]
    review = _parse_json(ValidatedSOS, review_data)
    extraction = _parse_json(ExtractedSOS, extraction_data)
    with pytest.raises(ValueError):
        review.assert_matches_extraction(extraction)


def test_accepted_as_reported_review_rejects_unrelated_existing_evidence() -> None:
    _, extraction_data = ordinary_case()
    review_data = reviewed_ordinary_case()
    review_data["fields"]["people_count"]["evidence_ids"] = ["access"]
    review = _parse_json(ValidatedSOS, review_data)
    extraction = _parse_json(ExtractedSOS, extraction_data)
    with pytest.raises(ValueError):
        review.assert_matches_extraction(extraction)


def test_accepted_as_reported_review_must_match_extracted_value() -> None:
    _, extraction_data = ordinary_case()
    review_data = reviewed_ordinary_case()
    review_data["fields"]["people_count"]["value"] = {"min": 3, "max": 3}
    review = _parse_json(ValidatedSOS, review_data)
    extraction = _parse_json(ExtractedSOS, extraction_data)
    with pytest.raises(ValueError):
        review.assert_matches_extraction(extraction)


def test_priority_reference_matches_its_extraction() -> None:
    _, extraction_data = ambiguous_case()
    assessment = _parse_json(PriorityAssessment, unassessed_priority_case())
    extraction = _parse_json(ExtractedSOS, extraction_data)
    assessment.assert_matches_extraction(extraction)


def test_priority_reference_rejects_a_different_extraction() -> None:
    _, extraction_data = critical_case()
    assessment = _parse_json(PriorityAssessment, unassessed_priority_case())
    extraction = _parse_json(ExtractedSOS, extraction_data)
    with pytest.raises(ValueError):
        assessment.assert_matches_extraction(extraction)
