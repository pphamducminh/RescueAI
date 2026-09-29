"""Focused offline demo extraction and priority tests."""

from __future__ import annotations

from datetime import UTC, datetime

from backend.domain.sos import (
    ExtractedSOS,
    FieldState,
    GeoPoint,
    LocationRole,
    LocationSource,
    SOSInput,
    SubmittedLocation,
    SuggestedAttention,
    validate_extraction_against_input,
)
from backend.extraction.rules import RuleBasedSOSExtractor
from backend.priority.rules import RuleBasedPriorityAssessor


def _source(
    text: str,
    *,
    incident_location: bool | None = None,
) -> SOSInput:
    location = None
    if incident_location is not None:
        location = SubmittedLocation(
            point=GeoPoint(lat=10.8, lon=106.7),
            role=LocationRole.INCIDENT if incident_location else LocationRole.REPORTER,
            source=LocationSource.MAP_PIN,
            accuracy_m=None,
            captured_at=None,
        )
    return SOSInput(
        schema_version="1.0",
        sos_id="demo-1",
        input_revision=1,
        raw_text=text,
        image_ref=None,
        submitted_location=location,
        received_at=datetime(2026, 9, 28, 8, 0, tzinfo=UTC),
        reported_event_at=None,
    )


def _extract(source: SOSInput) -> ExtractedSOS:
    result = RuleBasedSOSExtractor().extract(source)
    validate_extraction_against_input(result, source)
    for item in result.evidence:
        if item.text_span is not None:
            assert (
                source.raw_text[item.text_span.start : item.text_span.end] == item.text_span.quote
            )
    return result


def test_unrecognized_text_stays_unknown_and_has_no_priority_weight() -> None:
    source = _source("Please help near the bridge.")
    extracted = _extract(source)
    assert all(
        getattr(extracted.facts, name).state is FieldState.UNKNOWN
        for name in type(extracted.facts).model_fields
    )
    assert extracted.evidence == ()
    assessment = RuleBasedPriorityAssessor().assess(extracted)
    assert assessment.suggested_attention is SuggestedAttention.INSUFFICIENT_INFORMATION
    assert assessment.priority_weight is None
    assert assessment.requires_human_review is True


def test_positive_english_phrases_have_exact_evidence_and_immediate_review() -> None:
    source = _source("2 people are trapped. One is unconscious. We need a boat.")
    extracted = _extract(source)
    assert extracted.facts.people_count.value is not None
    assert extracted.facts.people_count.value.min == 2
    assert extracted.facts.trapped.value is True
    assert extracted.facts.urgent_signs.state is FieldState.SUPPORTED
    assert extracted.facts.injury_reported.state is FieldState.UNKNOWN
    assert extracted.facts.requested_assistance.value == ("boat",)
    assert {item.text_span.quote for item in extracted.evidence if item.text_span} == {
        "2 people",
        "trapped",
        "unconscious",
        "need a boat",
    }
    assessment = RuleBasedPriorityAssessor().assess(extracted)
    assert assessment.suggested_attention is SuggestedAttention.IMMEDIATE_REVIEW
    assert assessment.priority_weight == 5.0
    assert assessment.based_on.snapshot_id == extracted.extraction_id


def test_explicit_vietnamese_false_values_are_distinct_from_unknown() -> None:
    source = _source("Có 2 người. Không ai bị thương hay mắc kẹt.")
    extracted = _extract(source)
    assert extracted.facts.injury_reported.state is FieldState.SUPPORTED
    assert extracted.facts.injury_reported.value is False
    assert extracted.facts.trapped.state is FieldState.SUPPORTED
    assert extracted.facts.trapped.value is False
    assert extracted.facts.urgent_signs.state is FieldState.UNKNOWN
    assessment = RuleBasedPriorityAssessor().assess(extracted)
    assert assessment.suggested_attention is SuggestedAttention.STANDARD_REVIEW
    assert assessment.priority_weight == 1.0
    assert "urgent_signs" in assessment.unresolved_fields


def test_zero_people_is_preserved_and_requires_review() -> None:
    extracted = _extract(_source("0 people remain at the site."))
    assert extracted.facts.people_count.state is FieldState.SUPPORTED
    assert extracted.facts.people_count.value is not None
    assert extracted.facts.people_count.value.min == 0
    assert extracted.facts.people_count.value.max == 0
    assessment = RuleBasedPriorityAssessor().assess(extracted)
    assert assessment.suggested_attention is SuggestedAttention.INSUFFICIENT_INFORMATION
    assert assessment.priority_weight is None
    assert "people_count" in assessment.unresolved_fields


def test_conflicting_counts_keep_both_claims() -> None:
    extracted = _extract(_source("First report: 2 people. Later report: 5 people."))
    count = extracted.facts.people_count
    assert count.state is FieldState.CONFLICTING
    assert count.value is None
    assert tuple(candidate.value.min for candidate in count.candidates if candidate.value) == (2, 5)
    assert len(count.candidates) == 2


def test_hedged_urgent_phrase_is_uncertain_and_not_positive() -> None:
    extracted = _extract(_source("Hình như một người bất tỉnh."))
    assert extracted.facts.urgent_signs.state is FieldState.UNCERTAIN
    assert extracted.facts.urgent_signs.value is None
    assert extracted.facts.urgent_signs.candidates[0].assertion == "hedged"
    assessment = RuleBasedPriorityAssessor().assess(extracted)
    assert assessment.suggested_attention is SuggestedAttention.INSUFFICIENT_INFORMATION


def test_narrow_symptom_denial_does_not_claim_empty_urgent_category() -> None:
    extracted = _extract(_source("No one is unconscious."))
    assert extracted.facts.urgent_signs.state is FieldState.UNKNOWN


def test_negated_requests_and_vietnamese_trapping_are_not_positive_claims() -> None:
    extracted = _extract(_source("Không ai mắc kẹt; không cần xuồng."))
    assert extracted.facts.trapped.state is FieldState.SUPPORTED
    assert extracted.facts.trapped.value is False
    assert extracted.facts.requested_assistance.state is FieldState.UNKNOWN


def test_hedged_numeric_count_is_uncertain_and_negated_count_is_unknown() -> None:
    hedged = _extract(_source("Maybe 2 people need help."))
    negated = _extract(_source("Not 2 people."))
    assert hedged.facts.people_count.state is FieldState.UNCERTAIN
    assert negated.facts.people_count.state is FieldState.UNKNOWN


def test_only_explicit_incident_role_gps_becomes_incident_location() -> None:
    incident = _extract(_source("Please help.", incident_location=True))
    reporter = _extract(_source("Please help.", incident_location=False))
    assert incident.facts.incident_location.state is FieldState.SUPPORTED
    assert incident.facts.incident_location.value is not None
    assert incident.facts.incident_location.value.point == GeoPoint(lat=10.8, lon=106.7)
    assert reporter.facts.incident_location.state is FieldState.UNKNOWN


def test_offline_extraction_and_assessment_replay_identically_and_round_trip() -> None:
    source = _source("5 người không thể rời đi. Nước đang dâng; cần xuồng.")
    extractor = RuleBasedSOSExtractor()
    first = extractor.extract(source)
    second = extractor.extract(source)
    assert first == second
    assert ExtractedSOS.model_validate_json(first.model_dump_json()) == first
    assessor = RuleBasedPriorityAssessor()
    assert assessor.assess(first) == assessor.assess(second)
    assert first.facts.trapped.value is True
    assert first.facts.water_signals.value == ("water_rising_reported",)
    assert first.facts.requested_assistance.value == ("boat",)
