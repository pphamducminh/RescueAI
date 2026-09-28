"""Synthetic SOS records for exercising the approved v1 data contract.

These are invented examples, not real incident reports or model predictions.
Each factory returns fresh JSON-compatible data so tests can mutate it safely.
"""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "1.0"
RECEIVED_AT = "2026-09-28T08:00:00+00:00"
EXTRACTED_AT = "2026-09-28T08:00:01+00:00"
REVIEWED_AT = "2026-09-28T08:05:00+00:00"

FACT_NAMES = (
    "people_count",
    "injury_reported",
    "urgent_signs",
    "vulnerable_groups",
    "trapped",
    "water_signals",
    "fire_signals",
    "structure_signals",
    "access_observations",
    "requested_assistance",
    "incident_location",
)


def _span(raw_text: str, quote: str) -> dict[str, Any]:
    start = raw_text.index(quote)
    return {"start": start, "end": start + len(quote), "quote": quote}


def _evidence(
    evidence_id: str,
    sos_id: str,
    *,
    raw_text: str | None = None,
    quote: str | None = None,
    source_type: str = "sos_text",
) -> dict[str, Any]:
    return {
        "id": evidence_id,
        "source_type": source_type,
        "source_ref": sos_id,
        "text_span": _span(raw_text, quote) if raw_text is not None and quote is not None else None,
        "image_box": None,
        "captured_at": None,
    }


def _candidate(
    value: Any,
    evidence_id: str,
    *,
    assertion: str = "stated",
    confidence: float | None = None,
) -> dict[str, Any]:
    return {
        "value": value,
        "assertion": assertion,
        "method": "direct_input",
        "evidence_ids": [evidence_id],
        "confidence": confidence,
    }


def _field(
    state: str,
    *,
    value: Any = None,
    candidates: list[dict[str, Any]] | None = None,
    confidence: float | None = None,
    unknown_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "state": state,
        "value": value,
        "candidates": [] if candidates is None else candidates,
        "confidence": confidence,
        "unknown_reason": unknown_reason,
    }


def _unknown(reason: str = "not_mentioned") -> dict[str, Any]:
    return _field("unknown", unknown_reason=reason)


def _supported(value: Any, evidence_id: str) -> dict[str, Any]:
    return _field("supported", value=value, candidates=[_candidate(value, evidence_id)])


def _all_unknown_facts() -> dict[str, Any]:
    return {name: _unknown() for name in FACT_NAMES}


def _sos_input(sos_id: str, raw_text: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "sos_id": sos_id,
        "input_revision": 1,
        "raw_text": raw_text,
        "image_ref": None,
        "submitted_location": None,
        "received_at": RECEIVED_AT,
        "reported_event_at": None,
    }


def _extraction(
    source: dict[str, Any], evidence: list[dict[str, Any]], facts: dict[str, Any]
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "extraction_id": f"extraction-{source['sos_id']}",
        "sos_id": source["sos_id"],
        "input_revision": source["input_revision"],
        "extractor": {
            "kind": "rule_based",
            "model_id": None,
            "version": "synthetic-fixture-v1",
            "prompt_version": None,
            "modalities_used": ["text", "gps"] if source["submitted_location"] else ["text"],
        },
        "extracted_at": EXTRACTED_AT,
        "evidence": evidence,
        "facts": facts,
    }


def ordinary_case() -> tuple[dict[str, Any], dict[str, Any]]:
    """Reported two people, explicit injury/trapping denials, and an incident map pin."""
    raw_text = (
        "Chúng tôi có 2 người tại điểm ghim. Không ai bị thương hay mắc kẹt. Đường bộ vào được."
    )
    source = _sos_input("sos-ordinary", raw_text)
    point = {"lat": 10.8, "lon": 106.7}
    source["submitted_location"] = {
        "point": point,
        "role": "incident",
        "source": "map_pin",
        "accuracy_m": None,
        "captured_at": None,
    }
    evidence = [
        _evidence("count", source["sos_id"], raw_text=raw_text, quote="2 người"),
        _evidence(
            "denial",
            source["sos_id"],
            raw_text=raw_text,
            quote="Không ai bị thương hay mắc kẹt",
        ),
        _evidence("access", source["sos_id"], raw_text=raw_text, quote="Đường bộ vào được"),
        _evidence("pin", source["sos_id"], source_type="submitted_gps"),
    ]
    facts = _all_unknown_facts()
    facts["people_count"] = _supported({"min": 2, "max": 2}, "count")
    facts["injury_reported"] = _supported(False, "denial")
    facts["trapped"] = _supported(False, "denial")
    facts["access_observations"] = _supported(
        [{"mode": "unspecified", "status": "passable_reported"}], "access"
    )
    facts["incident_location"] = _supported(
        {"description": None, "point": point, "precision": "point"}, "pin"
    )
    return source, _extraction(source, evidence, facts)


def critical_case() -> tuple[dict[str, Any], dict[str, Any]]:
    """Reported urgent signs with no supplied incident location."""
    raw_text = (
        "Có 5 người trên mái nhà, 2 trẻ nhỏ, không thể rời đi. "
        "Nước đang dâng; một người bất tỉnh. Xe không vào được, cần xuồng."
    )
    source = _sos_input("sos-critical", raw_text)
    phrases = {
        "count": "5 người",
        "children": "2 trẻ nhỏ",
        "trapped": "không thể rời đi",
        "water": "Nước đang dâng",
        "urgent": "bất tỉnh",
        "access": "Xe không vào được",
        "boat": "cần xuồng",
    }
    evidence = [
        _evidence(key, source["sos_id"], raw_text=raw_text, quote=quote)
        for key, quote in phrases.items()
    ]
    facts = _all_unknown_facts()
    facts["people_count"] = _supported({"min": 5, "max": 5}, "count")
    facts["vulnerable_groups"] = _supported(
        [{"group": "child", "count": {"min": 2, "max": 2}}], "children"
    )
    facts["trapped"] = _supported(True, "trapped")
    facts["water_signals"] = _supported(["water_rising_reported"], "water")
    facts["urgent_signs"] = _supported(["unconscious_reported"], "urgent")
    facts["access_observations"] = _supported(
        [{"mode": "vehicle", "status": "blocked_reported"}], "access"
    )
    facts["requested_assistance"] = _supported(["boat"], "boat")
    return source, _extraction(source, evidence, facts)


def ambiguous_case() -> tuple[dict[str, Any], dict[str, Any]]:
    """Vague people count and hedged symptom; GPS is explicitly the reporter's."""
    raw_text = "Bên kia cầu có vài người, hình như một người ngất. GPS này là chỗ tôi đang đứng."
    source = _sos_input("sos-ambiguous", raw_text)
    source["submitted_location"] = {
        "point": {"lat": 10.81, "lon": 106.71},
        "role": "reporter",
        "source": "device_gps",
        "accuracy_m": None,
        "captured_at": None,
    }
    evidence = [
        _evidence("vague_count", source["sos_id"], raw_text=raw_text, quote="vài người"),
        _evidence(
            "hedged_urgent",
            source["sos_id"],
            raw_text=raw_text,
            quote="hình như một người ngất",
        ),
    ]
    facts = _all_unknown_facts()
    facts["people_count"] = _field(
        "uncertain", candidates=[_candidate(None, "vague_count", assertion="hedged")]
    )
    facts["urgent_signs"] = _field(
        "uncertain",
        candidates=[_candidate(["unconscious_reported"], "hedged_urgent", assertion="hedged")],
    )
    return source, _extraction(source, evidence, facts)


def conflicting_case() -> tuple[dict[str, Any], dict[str, Any]]:
    """Two incompatible counts and opposing fire claims at the same location."""
    raw_text = (
        "Tin đầu nói có 2 người trong căn nhà này; hàng xóm lại nói có 5 người "
        "trong chính căn nhà đó. Một người bảo đang cháy, người kia bảo không cháy."
    )
    source = _sos_input("sos-conflicting", raw_text)
    phrases = {
        "count_two": "2 người",
        "count_five": "5 người",
        "fire_yes": "đang cháy",
        "fire_no": "không cháy",
    }
    evidence = [
        _evidence(key, source["sos_id"], raw_text=raw_text, quote=quote)
        for key, quote in phrases.items()
    ]
    facts = _all_unknown_facts()
    facts["people_count"] = _field(
        "conflicting",
        candidates=[
            _candidate({"min": 2, "max": 2}, "count_two"),
            _candidate({"min": 5, "max": 5}, "count_five"),
        ],
    )
    facts["fire_signals"] = _field(
        "conflicting",
        candidates=[
            _candidate(["fire_reported"], "fire_yes"),
            _candidate([], "fire_no"),
        ],
    )
    return source, _extraction(source, evidence, facts)


def reviewed_ordinary_case() -> dict[str, Any]:
    """A partial dispatcher review that accepts the reported incident point."""
    source, extraction = ordinary_case()
    fields: dict[str, Any] = {
        name: {
            "review_state": "not_reviewed",
            "value": None,
            "evidence_ids": [],
            "reviewer_id": None,
            "reviewed_at": None,
            "reason": None,
        }
        for name in FACT_NAMES
    }
    fields["people_count"] = {
        "review_state": "accepted_as_reported",
        "value": {"min": 2, "max": 2},
        "evidence_ids": ["count"],
        "reviewer_id": "dispatcher-1",
        "reviewed_at": REVIEWED_AT,
        "reason": None,
    }
    fields["incident_location"] = {
        "review_state": "accepted_as_reported",
        "value": extraction["facts"]["incident_location"]["value"],
        "evidence_ids": ["pin"],
        "reviewer_id": "dispatcher-1",
        "reviewed_at": REVIEWED_AT,
        "reason": None,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "validation_id": "validation-sos-ordinary",
        "sos_id": source["sos_id"],
        "extraction_id": extraction["extraction_id"],
        "revision": 1,
        "fields": fields,
        "location_resolution": {
            "quality": "reported_point",
            "point": source["submitted_location"]["point"],
            "description": None,
            "routing_anchor": {
                "graph_node_id": "node-1",
                "snapped_distance_m": 12.0,
                "accepted_by": "dispatcher-1",
                "accepted_at": REVIEWED_AT,
            },
        },
        "overall_review_state": "partial",
        "updated_at": REVIEWED_AT,
    }


def unassessed_priority_case() -> dict[str, Any]:
    """A provisional recommendation with no invented priority rule or weight."""
    _, extraction = ambiguous_case()
    return {
        "schema_version": SCHEMA_VERSION,
        "assessment_id": "assessment-sos-ambiguous",
        "sos_id": extraction["sos_id"],
        "based_on": {"stage": "extracted", "snapshot_id": extraction["extraction_id"]},
        "policy_version": "synthetic-fixture-policy-v1",
        "assessed_at": EXTRACTED_AT,
        "suggested_attention": "insufficient_information",
        "priority_weight": None,
        "reasons": [],
        "unresolved_fields": ["people_count", "incident_location"],
        "requires_human_review": True,
    }


def synthetic_ai_text_case(
    confidence: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """AI-shaped test data only; no model was run to produce this record."""
    source, extraction = ordinary_case()
    extraction["extractor"]["kind"] = "ai"
    extraction["extractor"]["model_id"] = "synthetic-contract-test-model"
    extraction["extractor"]["prompt_version"] = "synthetic-contract-test-prompt-v1"
    count = extraction["facts"]["people_count"]
    count["confidence"] = confidence
    count["candidates"][0]["method"] = "ai_text"
    count["candidates"][0]["confidence"] = confidence
    return source, extraction


def synthetic_ai_image_case() -> tuple[dict[str, Any], dict[str, Any]]:
    """An image-region provenance record without an image model or image asset."""
    source, extraction = synthetic_ai_text_case()
    source["image_ref"] = "synthetic-image-reference"
    extraction["extractor"]["modalities_used"].append("image")
    extraction["evidence"].append(
        {
            "id": "smoke_region",
            "source_type": "sos_image",
            "source_ref": source["image_ref"],
            "text_span": None,
            "image_box": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4},
            "captured_at": None,
        }
    )
    smoke = _supported(["smoke_observed"], "smoke_region")
    smoke["candidates"][0]["method"] = "ai_image"
    extraction["facts"]["fire_signals"] = smoke
    return source, extraction
