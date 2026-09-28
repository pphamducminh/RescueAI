"""Dispatcher review snapshots for SOS facts and incident locations.

These models record what a dispatcher has reviewed. They do not approve an
assignment or establish that a reported claim is externally true.
"""

from __future__ import annotations

from typing import Generic, Self, TypeVar

from pydantic import Field, StrictBool, StrictInt, model_validator

from backend.domain.sos_extraction import ExtractedSOS, _semantic_value
from backend.domain.sos_types import (
    AccessObservation,
    AssistanceTag,
    CountRange,
    FiniteNonNegative,
    FireTag,
    GeoPoint,
    LocationCandidate,
    LocationQuality,
    NonBlankString,
    OverallReviewState,
    ReviewState,
    SchemaVersion,
    SOSModel,
    StructureTag,
    UrgentSign,
    UTCDateTime,
    VulnerableGroup,
    WaterTag,
)

T = TypeVar("T")


class FieldResolution(SOSModel, Generic[T]):
    """One fact's explicit dispatcher review result.

    A null ``value`` in ``unresolved`` or ``not_reviewed`` cannot be mistaken
    for an asserted ``False``, empty list, or zero.
    """

    review_state: ReviewState
    value: T | None = Field(...)
    evidence_ids: tuple[NonBlankString, ...]
    reviewer_id: NonBlankString | None = Field(...)
    reviewed_at: UTCDateTime | None = Field(...)
    reason: NonBlankString | None = Field(...)

    @model_validator(mode="after")
    def check_review_state(self) -> Self:
        state = self.review_state.value
        resolved = {
            "accepted_as_reported",
            "corrected_by_dispatcher",
            "externally_confirmed",
        }
        if state in resolved:
            if self.value is None:
                raise ValueError("a resolved field requires a non-null value")
            if not self.evidence_ids:
                raise ValueError("a resolved field requires evidence_ids")
            if self.reviewer_id is None or self.reviewed_at is None:
                raise ValueError("a resolved field requires reviewer_id and reviewed_at")
        elif self.value is not None:
            raise ValueError("an unresolved or unreviewed field must have a null value")

        if (self.reviewer_id is None) != (self.reviewed_at is None):
            raise ValueError("reviewer_id and reviewed_at must be supplied together")
        if state == "not_reviewed" and (self.reviewer_id is not None or self.evidence_ids):
            raise ValueError("a not_reviewed field cannot have a reviewer or evidence")
        return self


class ReviewedFacts(SOSModel):
    """Every SOS fact has its own typed and explicitly present resolution."""

    people_count: FieldResolution[CountRange]
    injury_reported: FieldResolution[StrictBool]
    urgent_signs: FieldResolution[tuple[UrgentSign, ...]]
    vulnerable_groups: FieldResolution[tuple[VulnerableGroup, ...]]
    trapped: FieldResolution[StrictBool]
    water_signals: FieldResolution[tuple[WaterTag, ...]]
    fire_signals: FieldResolution[tuple[FireTag, ...]]
    structure_signals: FieldResolution[tuple[StructureTag, ...]]
    access_observations: FieldResolution[tuple[AccessObservation, ...]]
    requested_assistance: FieldResolution[tuple[AssistanceTag, ...]]
    incident_location: FieldResolution[LocationCandidate]


class RoutingAnchor(SOSModel):
    """A dispatcher-accepted graph match for a reviewed incident point."""

    graph_node_id: NonBlankString
    snapped_distance_m: FiniteNonNegative
    accepted_by: NonBlankString
    accepted_at: UTCDateTime


class LocationResolution(SOSModel):
    """Incident-location quality and optional graph anchor after review."""

    quality: LocationQuality
    point: GeoPoint | None = Field(...)
    description: NonBlankString | None = Field(...)
    routing_anchor: RoutingAnchor | None = Field(...)

    @model_validator(mode="after")
    def check_location_quality(self) -> Self:
        point_quality = self.quality.value in {
            "externally_confirmed_point",
            "reported_point",
        }
        if point_quality and self.point is None:
            raise ValueError("point quality requires an incident point")
        if not point_quality and self.point is not None:
            raise ValueError("only point quality may carry an incident point")
        if self.quality.value in {"approximate_area", "landmark_only"} and self.description is None:
            raise ValueError("area or landmark quality requires a description")
        if self.routing_anchor is not None and not point_quality:
            raise ValueError("a routing anchor requires point quality")
        return self


class ValidatedSOS(SOSModel):
    """Immutable versioned snapshot of dispatcher-reviewed SOS facts."""

    schema_version: SchemaVersion
    validation_id: NonBlankString
    sos_id: NonBlankString
    extraction_id: NonBlankString
    revision: StrictInt = Field(ge=1)
    fields: ReviewedFacts
    location_resolution: LocationResolution
    overall_review_state: OverallReviewState
    updated_at: UTCDateTime

    @model_validator(mode="after")
    def check_review_and_anchor(self) -> Self:
        if self.overall_review_state.value == "complete":
            for field_name in type(self.fields).model_fields:
                resolution = getattr(self.fields, field_name)
                if resolution.review_state.value == "not_reviewed":
                    raise ValueError(f"complete review cannot leave {field_name} not_reviewed")

        if self.location_resolution.quality.value in {
            "externally_confirmed_point",
            "reported_point",
        }:
            incident = self.fields.incident_location
            if incident.review_state.value not in {
                "accepted_as_reported",
                "corrected_by_dispatcher",
                "externally_confirmed",
            }:
                raise ValueError("point quality requires a reviewed incident field")
            if (
                incident.value is None
                or incident.value.precision.value != "point"
                or incident.value.point != self.location_resolution.point
            ):
                raise ValueError("reviewed incident point must match location resolution")
            if (
                self.location_resolution.quality.value == "externally_confirmed_point"
                and incident.review_state.value != "externally_confirmed"
            ):
                raise ValueError("externally confirmed point requires confirmed incident field")

        if self.location_resolution.routing_anchor is not None:
            incident = self.fields.incident_location
            if incident.review_state.value not in {
                "accepted_as_reported",
                "corrected_by_dispatcher",
                "externally_confirmed",
            }:
                raise ValueError("a routing anchor requires a reviewed incident location")
            if incident.value is None or incident.value.precision.value != "point":
                raise ValueError("a routing anchor requires a reviewed incident point")
            if incident.value.point != self.location_resolution.point:
                raise ValueError("routing anchor point must match the reviewed incident point")
        return self

    def assert_matches_extraction(self, extraction: ExtractedSOS) -> None:
        """Check source links for claims accepted directly from the extraction.

        Corrections and external confirmations may cite evidence collected after
        extraction, so their evidence IDs need a separate review evidence store.
        """

        if self.sos_id != extraction.sos_id or self.extraction_id != extraction.extraction_id:
            raise ValueError("validated SOS must reference its source extraction")
        if self.schema_version != extraction.schema_version:
            raise ValueError("validated SOS and extraction schema versions must match")

        for field_name in type(self.fields).model_fields:
            resolution = getattr(self.fields, field_name)
            if resolution.review_state.value != "accepted_as_reported":
                continue
            source_fact = getattr(extraction.facts, field_name)
            if source_fact.state.value != "supported" or _semantic_value(
                resolution.value
            ) != _semantic_value(source_fact.value):
                raise ValueError(f"{field_name} must match a supported extracted fact")
            supporting_evidence_ids = {
                evidence_id
                for candidate in source_fact.candidates
                if candidate.assertion.value == "stated"
                and _semantic_value(candidate.value) == _semantic_value(source_fact.value)
                for evidence_id in candidate.evidence_ids
            }
            if not set(resolution.evidence_ids).issubset(supporting_evidence_ids):
                raise ValueError(f"{field_name} must cite evidence for its accepted value")
