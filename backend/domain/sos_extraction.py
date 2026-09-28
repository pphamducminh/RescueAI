"""Original SOS input and evidence-backed extraction snapshots (schema v1)."""

from enum import Enum
from typing import Generic, Self, TypeVar

from pydantic import BaseModel, Field, StrictBool, StrictInt, model_validator

from backend.domain.sos_types import (
    AccessObservation,
    Assertion,
    AssistanceTag,
    CandidateMethod,
    Confidence,
    CountRange,
    EvidenceSource,
    ExtractorKind,
    FieldState,
    FiniteNonNegative,
    FireTag,
    GeoPoint,
    LocationCandidate,
    LocationRole,
    LocationSource,
    Modality,
    NonBlankString,
    SchemaVersion,
    SOSModel,
    StrictFiniteFloat,
    StructureTag,
    UnknownReason,
    UrgentSign,
    UTCDateTime,
    VulnerableGroup,
    WaterTag,
)

T = TypeVar("T")
DIRECT_ASSERTION_SOURCES = frozenset(
    {
        EvidenceSource.SOS_TEXT,
        EvidenceSource.DISPATCHER_INPUT,
        EvidenceSource.REPORTER_FOLLOWUP,
        EvidenceSource.RESPONDER_OBSERVATION,
    }
)


def _semantic_value(value: object) -> object:
    """Compare unordered fact tag collections without treating order as a conflict."""
    if isinstance(value, BaseModel):
        return tuple(sorted((name, _semantic_value(item)) for name, item in value.__dict__.items()))
    if isinstance(value, (list, tuple)):
        return tuple(sorted((_semantic_value(item) for item in value), key=repr))
    if isinstance(value, Enum):
        return value.value
    return value


def _needs_explicit_denial(value: object) -> bool:
    return (
        value is False
        or isinstance(value, tuple)
        and not value
        or isinstance(value, CountRange)
        and value.min == 0
        and value.max == 0
    )


def _count_ranges_conflict(left: CountRange, right: CountRange) -> bool:
    return (left.max is not None and left.max < right.min) or (
        right.max is not None and right.max < left.min
    )


def _values_conflict(left: object, right: object) -> bool:
    """Detect structural contradictions without treating additive tags as exclusive."""
    if left is None or right is None:
        return False
    if isinstance(left, CountRange) and isinstance(right, CountRange):
        return _count_ranges_conflict(left, right)
    if isinstance(left, tuple) and isinstance(right, tuple):
        if not left or not right:
            return bool(left) != bool(right)
        if all(isinstance(item, AccessObservation) for item in (*left, *right)):
            return any(a.mode == b.mode and a.status != b.status for a in left for b in right)
        if all(isinstance(item, VulnerableGroup) for item in (*left, *right)):
            return any(
                a.group == b.group
                and a.count is not None
                and b.count is not None
                and _count_ranges_conflict(a.count, b.count)
                for a in left
                for b in right
            )
        return False
    return _semantic_value(left) != _semantic_value(right)


class TextSpan(SOSModel):
    start: StrictInt = Field(ge=0)
    end: StrictInt = Field(gt=0)
    quote: NonBlankString

    @model_validator(mode="after")
    def check_offsets(self) -> Self:
        if self.end <= self.start:
            raise ValueError("text span end must exceed start")
        if len(self.quote) != self.end - self.start:
            raise ValueError("text span length must match quote length")
        return self


class ImageBox(SOSModel):
    x: StrictFiniteFloat = Field(ge=0, le=1)
    y: StrictFiniteFloat = Field(ge=0, le=1)
    w: StrictFiniteFloat = Field(gt=0, le=1)
    h: StrictFiniteFloat = Field(gt=0, le=1)

    @model_validator(mode="after")
    def check_bounds(self) -> Self:
        if self.x + self.w > 1 + 1e-12 or self.y + self.h > 1 + 1e-12:
            raise ValueError("image box must fit within normalized image bounds")
        return self


class Evidence(SOSModel):
    id: NonBlankString
    source_type: EvidenceSource
    source_ref: NonBlankString
    text_span: TextSpan | None = Field(...)
    image_box: ImageBox | None = Field(...)
    captured_at: UTCDateTime | None = Field(...)

    @model_validator(mode="after")
    def check_source_shape(self) -> Self:
        if self.source_type is EvidenceSource.SOS_TEXT:
            if self.text_span is None or self.image_box is not None:
                raise ValueError("SOS text evidence requires a text span and no image box")
        elif self.source_type is EvidenceSource.SOS_IMAGE:
            if self.image_box is None or self.text_span is not None:
                raise ValueError("SOS image evidence requires an image box and no text span")
        elif self.source_type is EvidenceSource.SUBMITTED_GPS:
            if self.text_span is not None or self.image_box is not None:
                raise ValueError("submitted GPS evidence cannot use a text span or image box")
        return self


class SubmittedLocation(SOSModel):
    point: GeoPoint
    role: LocationRole
    source: LocationSource
    accuracy_m: FiniteNonNegative | None = Field(...)
    captured_at: UTCDateTime | None = Field(...)


class ExtractorInfo(SOSModel):
    kind: ExtractorKind
    model_id: NonBlankString | None = Field(...)
    version: NonBlankString
    prompt_version: NonBlankString | None = Field(...)
    modalities_used: tuple[Modality, ...]

    @model_validator(mode="after")
    def check_modalities(self) -> Self:
        if len(set(self.modalities_used)) != len(self.modalities_used):
            raise ValueError("modalities_used must not contain duplicates")
        return self


class Candidate(SOSModel, Generic[T]):
    value: T | None = Field(...)
    assertion: Assertion
    method: CandidateMethod
    evidence_ids: tuple[NonBlankString, ...]
    confidence: Confidence | None = Field(...)

    @model_validator(mode="after")
    def check_evidence_ids(self) -> Self:
        if not self.evidence_ids:
            raise ValueError("a candidate requires at least one evidence ID")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("candidate evidence IDs must be unique")
        return self


class EvidenceField(SOSModel, Generic[T]):
    state: FieldState
    value: T | None = Field(...)
    candidates: tuple[Candidate[T], ...]
    confidence: Confidence | None = Field(...)
    unknown_reason: UnknownReason | None = Field(...)

    @model_validator(mode="after")
    def check_state(self) -> Self:
        if self.state is FieldState.SUPPORTED:
            if self.value is None or not self.candidates or self.unknown_reason is not None:
                raise ValueError("supported field requires a value and candidate evidence")
            if isinstance(self.value, tuple):
                expected_items = {_semantic_value(item) for item in self.value}
                supported_items: set[object] = set()
                for candidate in self.candidates:
                    if candidate.assertion is not Assertion.STATED or candidate.value is None:
                        continue
                    if not isinstance(candidate.value, tuple):
                        raise ValueError("list fact candidate must contain a tag list")
                    candidate_items = {_semantic_value(item) for item in candidate.value}
                    if not candidate_items.issubset(expected_items):
                        raise ValueError("stated tags must all appear in the supported value")
                    if expected_items and not candidate_items:
                        raise ValueError("an explicit denial conflicts with positive tags")
                    supported_items.update(candidate_items)
                if supported_items != expected_items or (
                    not expected_items
                    and not any(
                        candidate.assertion is Assertion.STATED and candidate.value == ()
                        for candidate in self.candidates
                    )
                ):
                    raise ValueError("every supported tag needs stated candidate evidence")
            else:
                expected = _semantic_value(self.value)
                if not any(
                    _semantic_value(candidate.value) == expected
                    and candidate.assertion is Assertion.STATED
                    for candidate in self.candidates
                ):
                    raise ValueError("supported value requires a matching stated candidate")
                if any(
                    _values_conflict(self.value, candidate.value) for candidate in self.candidates
                ):
                    raise ValueError("contradictory candidates require conflicting state")
        else:
            if self.value is not None or self.confidence is not None:
                raise ValueError("non-supported fields require null value and confidence")
            if self.state is FieldState.UNKNOWN:
                if self.candidates or self.unknown_reason is None:
                    raise ValueError("unknown field requires no candidates and an unknown reason")
            elif self.state is FieldState.UNCERTAIN:
                if not self.candidates or self.unknown_reason is not None:
                    raise ValueError("uncertain field requires candidates and no unknown reason")
            elif self.state is FieldState.CONFLICTING:
                if len(self.candidates) < 2 or self.unknown_reason is not None:
                    raise ValueError("conflicting field requires at least two candidates")
                known_values = [
                    candidate.value for candidate in self.candidates if candidate.value is not None
                ]
                if not any(
                    _values_conflict(left, right)
                    for index, left in enumerate(known_values)
                    for right in known_values[index + 1 :]
                ):
                    raise ValueError("conflicting candidates must carry incompatible claims")
        return self


class ExtractedFacts(SOSModel):
    people_count: EvidenceField[CountRange]
    injury_reported: EvidenceField[StrictBool]
    urgent_signs: EvidenceField[tuple[UrgentSign, ...]]
    vulnerable_groups: EvidenceField[tuple[VulnerableGroup, ...]]
    trapped: EvidenceField[StrictBool]
    water_signals: EvidenceField[tuple[WaterTag, ...]]
    fire_signals: EvidenceField[tuple[FireTag, ...]]
    structure_signals: EvidenceField[tuple[StructureTag, ...]]
    access_observations: EvidenceField[tuple[AccessObservation, ...]]
    requested_assistance: EvidenceField[tuple[AssistanceTag, ...]]
    incident_location: EvidenceField[LocationCandidate]


class SOSInput(SOSModel):
    schema_version: SchemaVersion
    sos_id: NonBlankString
    input_revision: StrictInt = Field(ge=1)
    raw_text: NonBlankString
    image_ref: NonBlankString | None = Field(...)
    submitted_location: SubmittedLocation | None = Field(...)
    received_at: UTCDateTime
    reported_event_at: UTCDateTime | None = Field(...)


class ExtractedSOS(SOSModel):
    schema_version: SchemaVersion
    extraction_id: NonBlankString
    sos_id: NonBlankString
    input_revision: StrictInt = Field(ge=1)
    extractor: ExtractorInfo
    extracted_at: UTCDateTime
    evidence: tuple[Evidence, ...]
    facts: ExtractedFacts

    @model_validator(mode="after")
    def check_evidence_graph(self) -> Self:
        evidence_by_id = {item.id: item for item in self.evidence}
        if len(evidence_by_id) != len(self.evidence):
            raise ValueError("evidence IDs must be unique")
        for field_name in type(self.facts).model_fields:
            fact: EvidenceField[object] = getattr(self.facts, field_name)
            for candidate in fact.candidates:
                if self.extractor.kind is ExtractorKind.RULE_BASED and candidate.method in {
                    CandidateMethod.AI_TEXT,
                    CandidateMethod.AI_IMAGE,
                }:
                    raise ValueError("rule-based output cannot use AI candidate methods")
                for evidence_id in candidate.evidence_ids:
                    evidence = evidence_by_id.get(evidence_id)
                    if evidence is None:
                        raise ValueError(f"candidate references missing evidence: {evidence_id}")
                    self._check_method_source(candidate.method, evidence.source_type)
                    if candidate.method is CandidateMethod.AI_TEXT and evidence.text_span is None:
                        raise ValueError("ai_text candidate requires an exact text span")
                    self._check_declared_modality(evidence.source_type)
                if candidate.value is not None and _needs_explicit_denial(candidate.value):
                    if not any(
                        evidence_by_id[evidence_id].source_type in DIRECT_ASSERTION_SOURCES
                        for evidence_id in candidate.evidence_ids
                    ):
                        raise ValueError("false, empty, or zero candidate needs direct evidence")
        return self

    @staticmethod
    def _check_method_source(method: CandidateMethod, source: EvidenceSource) -> None:
        if method is CandidateMethod.AI_TEXT and source not in {
            EvidenceSource.SOS_TEXT,
            EvidenceSource.REPORTER_FOLLOWUP,
            EvidenceSource.RESPONDER_OBSERVATION,
        }:
            raise ValueError("ai_text candidate needs textual evidence")
        if method is CandidateMethod.AI_IMAGE and source is not EvidenceSource.SOS_IMAGE:
            raise ValueError("ai_image candidate needs SOS image evidence")
        if method is CandidateMethod.DIRECT_INPUT and source in {
            EvidenceSource.SOS_IMAGE,
            EvidenceSource.SENSOR_DATA,
        }:
            raise ValueError("direct_input candidate cannot cite image or sensor evidence")
        if method is CandidateMethod.SENSOR and source is not EvidenceSource.SENSOR_DATA:
            raise ValueError("sensor candidate needs sensor evidence")
        if source is EvidenceSource.SUBMITTED_GPS and method is not CandidateMethod.DIRECT_INPUT:
            raise ValueError("submitted GPS must be recorded as direct input")

    def _check_declared_modality(self, source: EvidenceSource) -> None:
        expected = {
            EvidenceSource.SOS_TEXT: Modality.TEXT,
            EvidenceSource.SOS_IMAGE: Modality.IMAGE,
            EvidenceSource.SUBMITTED_GPS: Modality.GPS,
        }.get(source)
        if expected is not None and expected not in self.extractor.modalities_used:
            raise ValueError(f"extractor must declare {expected.value} modality")


def validate_extraction_against_input(extraction: ExtractedSOS, source: SOSInput) -> None:
    """Check links and source grounding that need both immutable snapshots."""
    if extraction.sos_id != source.sos_id or extraction.input_revision != source.input_revision:
        raise ValueError("extraction must match the SOS input ID and revision")
    for evidence in extraction.evidence:
        if evidence.source_type in {EvidenceSource.SOS_TEXT, EvidenceSource.SUBMITTED_GPS}:
            if evidence.source_ref != source.sos_id:
                raise ValueError("SOS text/GPS source_ref must identify the SOS input")
        if evidence.source_type is EvidenceSource.SOS_TEXT:
            span = evidence.text_span
            if span is None or source.raw_text[span.start : span.end] != span.quote:
                raise ValueError("text evidence span must quote the unchanged SOS input")
        elif evidence.source_type is EvidenceSource.SOS_IMAGE:
            if source.image_ref is None or evidence.source_ref != source.image_ref:
                raise ValueError("image evidence must refer to the submitted image")
        elif evidence.source_type is EvidenceSource.SUBMITTED_GPS:
            if source.submitted_location is None:
                raise ValueError("GPS evidence requires a submitted location")
    location_field = extraction.facts.incident_location
    for candidate in location_field.candidates:
        location = candidate.value
        if location is None or location.point is None:
            continue
        submitted = source.submitted_location
        if submitted is None or submitted.role is not LocationRole.INCIDENT:
            raise ValueError("an extracted incident point requires explicit incident-role GPS/pin")
        if location.point != submitted.point:
            raise ValueError("an extracted incident point must copy the submitted coordinates")
        if candidate.method is not CandidateMethod.DIRECT_INPUT:
            raise ValueError("submitted incident coordinates must be direct input")
        evidence_by_id = {item.id: item for item in extraction.evidence}
        if not any(
            evidence_by_id[evidence_id].source_type is EvidenceSource.SUBMITTED_GPS
            for evidence_id in candidate.evidence_ids
        ):
            raise ValueError("incident point candidate requires submitted GPS evidence")
