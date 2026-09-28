"""Strict private-record schema for the offline synthetic SOS text benchmark.

Gold labels describe the emitted report and its supplied metadata. They are
deliberately separate from both the simulated incident and extractor output.
"""

import json
from collections import Counter
from enum import Enum, StrEnum
from typing import Any, Generic, Literal, Self, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from backend.domain.sos_extraction import Evidence, SOSInput
from backend.domain.sos_types import (
    AccessObservation,
    Assertion,
    AssistanceTag,
    CountRange,
    EvidenceSource,
    FieldState,
    FireTag,
    GeoPoint,
    LocationCandidate,
    LocationPrecision,
    LocationRole,
    NonBlankString,
    SchemaVersion,
    SOSModel,
    StructureTag,
    UnknownReason,
    UrgentSign,
    UTCDateTime,
    VulnerableGroup,
    VulnerableGroupKind,
    WaterTag,
)

T = TypeVar("T")


class Split(StrEnum):
    TRAIN = "train"
    DEV = "dev"
    TEST = "test"


class SignalBucket(StrEnum):
    LOWER = "lower"
    INTERMEDIATE = "intermediate"
    HIGH = "high"


class PrimaryChallenge(StrEnum):
    ORDINARY = "ordinary"
    NOISY = "noisy"
    MISSING = "missing"
    VAGUE = "vague"
    CONFLICTING = "conflicting"


class CountMode(StrEnum):
    EXACT = "exact"
    RANGE = "range"
    VAGUE = "vague"
    OMITTED = "omitted"
    CONFLICTING = "conflicting"


class InjuryMode(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    OMITTED = "omitted"
    HEDGED = "hedged"
    CONFLICTING = "conflicting"


class LocationMode(StrEnum):
    INCIDENT_PIN = "incident_pin"
    TEXT_LOCATION = "text_location"
    REPORTER_GPS = "reporter_gps"
    ABSENT = "absent"
    COMPETING = "competing"


class StyleFlag(StrEnum):
    INFORMAL = "informal"
    TYPO = "typo"
    FRAGMENT = "fragment"
    IRRELEVANT = "irrelevant"
    URGENCY = "urgency"


class FactPath(StrEnum):
    PEOPLE_COUNT = "people_count"
    INJURY_REPORTED = "injury_reported"
    URGENT_SIGNS = "urgent_signs"
    VULNERABLE_GROUPS = "vulnerable_groups"
    TRAPPED = "trapped"
    WATER_SIGNALS = "water_signals"
    FIRE_SIGNALS = "fire_signals"
    STRUCTURE_SIGNALS = "structure_signals"
    ACCESS_OBSERVATIONS = "access_observations"
    REQUESTED_ASSISTANCE = "requested_assistance"
    INCIDENT_LOCATION = "incident_location"


class BenchmarkMetadata(SOSModel):
    origin: Literal["synthetic_template_rule"]
    language: Literal["vi"]
    generator: Literal["offline_templates_v1"]
    master_seed: StrictInt
    sample: StrictBool


class Versions(SOSModel):
    # ``schema`` is the wire key; ``BaseModel.schema`` is a legacy class method.
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)

    schema_version: SchemaVersion = Field(alias="schema")
    generator: NonBlankString
    rubric: NonBlankString
    phrase_bank: NonBlankString
    phrase_bank_sha256: NonBlankString = Field(pattern=r"^[0-9a-f]{64}$")
    schema_source_sha256: NonBlankString = Field(pattern=r"^[0-9a-f]{64}$")
    generator_source_sha256: NonBlankString = Field(pattern=r"^[0-9a-f]{64}$")
    rubric_source_sha256: NonBlankString = Field(pattern=r"^[0-9a-f]{64}$")
    python_version: NonBlankString
    pydantic_version: NonBlankString
    prng: Literal["python_random_mt19937"]


class GenerationMetadata(SOSModel):
    case_seed: StrictInt = Field(ge=0)
    template_family_id: NonBlankString
    incident_group_id: NonBlankString
    paired_variant_group_id: NonBlankString | None = Field(...)
    primary_challenge: PrimaryChallenge
    count_mode: CountMode
    injury_mode: InjuryMode
    location_mode: LocationMode
    signal_bucket: SignalBucket
    latent_signal_bucket: SignalBucket
    style_flags: tuple[StyleFlag, ...]
    attempt: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def check_flags(self) -> Self:
        if len(set(self.style_flags)) != len(self.style_flags):
            raise ValueError("style flags must be unique")
        return self


class LatentScenario(SOSModel):
    people_count: StrictInt = Field(ge=0)
    injury_present: StrictBool
    urgent_sign: UrgentSign | None = Field(...)
    trapped: StrictBool
    water_active: StrictBool
    fire_active: StrictBool
    structure_active: StrictBool
    access_blocked: StrictBool
    vulnerable_group: VulnerableGroupKind | None = Field(...)
    location_kind: LocationPrecision
    location_role: Literal["incident"]
    incident_point: GeoPoint
    incident_landmark: NonBlankString
    incident_time: UTCDateTime

    @model_validator(mode="after")
    def check_empty_incident(self) -> Self:
        if self.people_count == 0 and (
            self.injury_present
            or self.urgent_sign is not None
            or self.trapped
            or self.vulnerable_group is not None
        ):
            raise ValueError("zero people cannot have person-specific incident facts")
        return self


class PlannedClaim(SOSModel):
    field_path: NonBlankString
    value: Any | None = Field(...)
    assertion: Assertion
    witness_id: NonBlankString
    scope_id: NonBlankString
    time_ref: NonBlankString


class CommunicationPlan(SOSModel):
    mentioned_fields: tuple[FactPath, ...]
    omitted_fields: tuple[FactPath, ...]
    hedged_fields: tuple[FactPath, ...]
    conflict_fields: tuple[FactPath, ...]
    noise_edits: tuple[NonBlankString, ...]
    planned_claims: tuple[PlannedClaim, ...]

    @model_validator(mode="after")
    def check_field_sets(self) -> Self:
        for name in ("mentioned_fields", "omitted_fields", "hedged_fields", "conflict_fields"):
            values = getattr(self, name)
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must not contain duplicates")
        mentioned = set(self.mentioned_fields)
        omitted = set(self.omitted_fields)
        if mentioned & omitted:
            raise ValueError("a fact cannot be both mentioned and omitted")
        if mentioned | omitted != set(FactPath):
            raise ValueError("every SOS fact must be mentioned or explicitly omitted")
        if not set(self.hedged_fields).issubset(mentioned):
            raise ValueError("hedged facts must be mentioned")
        if not set(self.conflict_fields).issubset(mentioned):
            raise ValueError("conflicting facts must be mentioned")
        if any(claim.field_path not in mentioned for claim in self.planned_claims):
            raise ValueError("planned claims must belong to mentioned fields")
        return self


def _semantic_value(value: object) -> object:
    """Compare normalized values without assigning meaning to tag ordering."""
    if isinstance(value, BaseModel):
        return tuple(sorted((key, _semantic_value(item)) for key, item in value.__dict__.items()))
    if isinstance(value, (list, tuple)):
        return tuple(sorted((_semantic_value(item) for item in value), key=repr))
    if isinstance(value, Enum):
        return value.value
    return value


def _ranges_conflict(left: CountRange, right: CountRange) -> bool:
    return (left.max is not None and left.max < right.min) or (
        right.max is not None and right.max < left.min
    )


def _values_conflict(left: object, right: object) -> bool:
    if left is None or right is None:
        return False
    if isinstance(left, CountRange) and isinstance(right, CountRange):
        return _ranges_conflict(left, right)
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
                and _ranges_conflict(a.count, b.count)
                for a in left
                for b in right
            )
        return False
    return _semantic_value(left) != _semantic_value(right)


class GoldClaim(SOSModel, Generic[T]):
    value: T | None = Field(...)
    assertion: Assertion
    evidence_ids: tuple[NonBlankString, ...]
    witness_id: NonBlankString
    scope_id: NonBlankString
    time_ref: NonBlankString

    @model_validator(mode="after")
    def check_evidence_ids(self) -> Self:
        if not self.evidence_ids or len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("a gold claim needs distinct evidence IDs")
        return self


class GoldField(SOSModel, Generic[T]):
    state: FieldState
    value: T | None = Field(...)
    unknown_reason: UnknownReason | None = Field(...)
    claims: tuple[GoldClaim[T], ...]

    @model_validator(mode="after")
    def check_state(self) -> Self:
        if self.state is FieldState.UNKNOWN:
            if self.value is not None or self.unknown_reason is None or self.claims:
                raise ValueError("unknown gold needs null value, a reason, and no claims")
            return self
        if self.value is not None and self.state is not FieldState.SUPPORTED:
            raise ValueError("uncertain and conflicting gold require null value")
        if self.unknown_reason is not None or not self.claims:
            raise ValueError("non-unknown gold requires claims and no unknown reason")
        if self.state is FieldState.UNCERTAIN:
            return self
        if self.state is FieldState.CONFLICTING:
            if len(self.claims) < 2:
                raise ValueError("conflicting gold requires at least two claims")
            if not any(
                left.assertion is Assertion.STATED
                and right.assertion is Assertion.STATED
                and left.witness_id != right.witness_id
                and left.scope_id == right.scope_id
                and left.time_ref == right.time_ref
                and _values_conflict(left.value, right.value)
                for index, left in enumerate(self.claims)
                for right in self.claims[index + 1 :]
            ):
                raise ValueError("conflicting gold needs incompatible same-scope witness claims")
            return self
        if self.value is None:
            raise ValueError("supported gold requires a non-null value")
        stated = [claim.value for claim in self.claims if claim.assertion is Assertion.STATED]
        if isinstance(self.value, tuple):
            expected = {_semantic_value(item) for item in self.value}
            found: set[object] = set()
            for claimed in stated:
                if not isinstance(claimed, tuple):
                    raise ValueError("supported list gold requires list-valued stated claims")
                items = {_semantic_value(item) for item in claimed}
                if not items.issubset(expected) or (expected and not items):
                    raise ValueError("stated list claims contradict supported gold")
                found.update(items)
            if found != expected or (not expected and () not in stated):
                raise ValueError("every supported tag needs stated claim evidence")
        elif not any(_semantic_value(claimed) == _semantic_value(self.value) for claimed in stated):
            raise ValueError("supported gold needs a matching stated claim")
        if any(_values_conflict(self.value, claim.value) for claim in self.claims):
            raise ValueError("contradictory claims require conflicting state")
        return self


class GoldFacts(SOSModel):
    people_count: GoldField[CountRange]
    injury_reported: GoldField[StrictBool]
    urgent_signs: GoldField[tuple[UrgentSign, ...]]
    vulnerable_groups: GoldField[tuple[VulnerableGroup, ...]]
    trapped: GoldField[StrictBool]
    water_signals: GoldField[tuple[WaterTag, ...]]
    fire_signals: GoldField[tuple[FireTag, ...]]
    structure_signals: GoldField[tuple[StructureTag, ...]]
    access_observations: GoldField[tuple[AccessObservation, ...]]
    requested_assistance: GoldField[tuple[AssistanceTag, ...]]
    incident_location: GoldField[LocationCandidate]


class GoldAnnotation(SOSModel):
    annotation_origin: Literal["render_trace"]
    facts: GoldFacts
    evidence: tuple[Evidence, ...]
    human_audit: Literal["pending"]


class GroundTruth(SOSModel):
    latent_scenario: LatentScenario
    communication_plan: CommunicationPlan
    gold_annotation: GoldAnnotation


class QualityCheck(SOSModel):
    schema_valid: StrictBool
    trace_valid: StrictBool
    human_review: Literal["pending"]


class BenchmarkRecord(SOSModel):
    case_id: NonBlankString
    synthetic: Literal[True]
    split: Split
    metadata: BenchmarkMetadata
    versions: Versions
    generation: GenerationMetadata
    ground_truth: GroundTruth
    generated_input: SOSInput
    qc: QualityCheck

    @model_validator(mode="after")
    def check_trace(self) -> Self:
        source = self.generated_input
        gold = self.ground_truth.gold_annotation
        if source.sos_id != self.case_id or source.schema_version != self.versions.schema_version:
            raise ValueError("case ID and schema version must match the generated SOS input")
        if source.image_ref is not None:
            raise ValueError("the v1 benchmark is text-only")
        if self.ground_truth.latent_scenario.incident_time > source.received_at:
            raise ValueError("latent incident time cannot follow SOS receipt")
        if not self.qc.schema_valid or not self.qc.trace_valid:
            raise ValueError("released records must pass automatic schema and trace checks")
        evidence_by_id = {item.id: item for item in gold.evidence}
        if len(evidence_by_id) != len(gold.evidence):
            raise ValueError("gold evidence IDs must be unique")
        for item in gold.evidence:
            if item.source_type is EvidenceSource.SOS_TEXT:
                span = item.text_span
                if (
                    item.source_ref != source.sos_id
                    or span is None
                    or source.raw_text[span.start : span.end] != span.quote
                ):
                    raise ValueError("gold text evidence must exactly quote generated input")
            elif item.source_type is EvidenceSource.SUBMITTED_GPS:
                if item.source_ref != source.sos_id or source.submitted_location is None:
                    raise ValueError("GPS evidence requires the submitted location")
            else:
                raise ValueError("the text-only benchmark supports SOS text/GPS evidence only")
        for field_name in GoldFacts.model_fields:
            field = getattr(gold.facts, field_name)
            for claim in field.claims:
                if any(evidence_id not in evidence_by_id for evidence_id in claim.evidence_ids):
                    raise ValueError(f"{field_name} claim cites absent evidence")
        planned_claims = Counter(
            json.dumps(claim.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
            for claim in self.ground_truth.communication_plan.planned_claims
        )
        gold_claims: Counter[str] = Counter()
        for field_name in GoldFacts.model_fields:
            for claim in getattr(gold.facts, field_name).claims:
                claim_data = claim.model_dump(mode="json")
                claim_data.pop("evidence_ids")
                claim_data["field_path"] = field_name
                gold_claims[json.dumps(claim_data, sort_keys=True, ensure_ascii=False)] += 1
        if planned_claims != gold_claims:
            raise ValueError("gold claims must preserve the pre-render communication plan")
        for claim in gold.facts.incident_location.claims:
            location = claim.value
            if location is None or location.point is None:
                continue
            submitted = source.submitted_location
            if (
                submitted is None
                or submitted.role is not LocationRole.INCIDENT
                or submitted.point != location.point
                or not any(
                    evidence_by_id[evidence_id].source_type is EvidenceSource.SUBMITTED_GPS
                    for evidence_id in claim.evidence_ids
                )
            ):
                raise ValueError("incident point requires matching incident-role GPS evidence")
        return self
