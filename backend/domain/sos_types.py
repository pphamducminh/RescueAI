"""Shared value types and exact categorical values for SOS schema v1."""

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal, Self, TypeAlias

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    FiniteFloat,
    StrictInt,
    StrictStr,
    model_validator,
)


def _require_nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


def _require_utc(value: datetime) -> datetime:
    if value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must use a UTC offset")
    return value


def _require_datetime_input(value: object) -> datetime | str:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and "T" in value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("timestamp must be ISO-8601 with an offset") from exc
    raise ValueError("timestamp must be an ISO-8601 string or datetime")


SchemaVersion: TypeAlias = Literal["1.0"]
NonBlankString: TypeAlias = Annotated[
    StrictStr, Field(min_length=1), AfterValidator(_require_nonblank)
]
UTCDateTime: TypeAlias = Annotated[
    AwareDatetime, BeforeValidator(_require_datetime_input), AfterValidator(_require_utc)
]
StrictFiniteFloat: TypeAlias = Annotated[FiniteFloat, Field(strict=True)]
FiniteNonNegative: TypeAlias = Annotated[FiniteFloat, Field(strict=True, ge=0)]
Confidence: TypeAlias = Annotated[FiniteFloat, Field(strict=True, ge=0, le=1)]


class SOSModel(BaseModel):
    """Immutable snapshot component with strict primitives and no extra fields."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class GeoPoint(SOSModel):
    lat: StrictFiniteFloat = Field(ge=-90, le=90)
    lon: StrictFiniteFloat = Field(ge=-180, le=180)


class CountRange(SOSModel):
    min: StrictInt = Field(ge=0)
    max: StrictInt | None = Field(...)

    @model_validator(mode="after")
    def check_bounds(self) -> Self:
        if self.max is not None and self.max < self.min:
            raise ValueError("max must be at least min")
        return self


class UrgentSign(StrEnum):
    UNCONSCIOUS_REPORTED = "unconscious_reported"
    BREATHING_DIFFICULTY_REPORTED = "breathing_difficulty_reported"
    SEVERE_BLEEDING_REPORTED = "severe_bleeding_reported"
    OTHER_URGENT_SYMPTOM_REPORTED = "other_urgent_symptom_reported"


class VulnerableGroupKind(StrEnum):
    CHILD = "child"
    OLDER_ADULT = "older_adult"
    PREGNANT_PERSON = "pregnant_person"
    DISABILITY_REPORTED = "disability_reported"
    LIMITED_MOBILITY_REPORTED = "limited_mobility_reported"
    OTHER_REPORTED = "other_reported"


class VulnerableGroup(SOSModel):
    group: VulnerableGroupKind
    count: CountRange | None = Field(...)


class WaterTag(StrEnum):
    FLOODING_REPORTED = "flooding_reported"
    WATER_RISING_REPORTED = "water_rising_reported"
    STRONG_CURRENT_REPORTED = "strong_current_reported"


class FireTag(StrEnum):
    FIRE_REPORTED = "fire_reported"
    FLAMES_OBSERVED = "flames_observed"
    SMOKE_OBSERVED = "smoke_observed"
    SPREADING_REPORTED = "spreading_reported"


class StructureTag(StrEnum):
    DAMAGE_REPORTED = "damage_reported"
    CRACKS_REPORTED = "cracks_reported"
    COLLAPSE_REPORTED = "collapse_reported"


class AccessMode(StrEnum):
    VEHICLE = "vehicle"
    FOOT = "foot"
    BOAT = "boat"
    UNSPECIFIED = "unspecified"


class AccessStatus(StrEnum):
    PASSABLE_REPORTED = "passable_reported"
    DIFFICULT_REPORTED = "difficult_reported"
    BLOCKED_REPORTED = "blocked_reported"


class AccessObservation(SOSModel):
    mode: AccessMode
    status: AccessStatus


class AssistanceTag(StrEnum):
    BOAT = "boat"
    AMBULANCE = "ambulance"
    MEDICAL_TEAM = "medical_team"
    EVACUATION = "evacuation"
    OTHER_REPORTED = "other_reported"


class LocationPrecision(StrEnum):
    POINT = "point"
    APPROXIMATE_AREA = "approximate_area"
    LANDMARK = "landmark"


class LocationCandidate(SOSModel):
    description: NonBlankString | None = Field(...)
    point: GeoPoint | None = Field(...)
    precision: LocationPrecision

    @model_validator(mode="after")
    def check_precision(self) -> Self:
        if self.precision is LocationPrecision.POINT and self.point is None:
            raise ValueError("point precision requires coordinates")
        if self.precision is not LocationPrecision.POINT and self.point is not None:
            raise ValueError("area and landmark precision cannot carry a precise point")
        if self.point is None and self.description is None:
            raise ValueError("location requires a description or point")
        return self


class LocationRole(StrEnum):
    INCIDENT = "incident"
    REPORTER = "reporter"
    UNSPECIFIED = "unspecified"


class LocationSource(StrEnum):
    DEVICE_GPS = "device_gps"
    MAP_PIN = "map_pin"
    TYPED_COORDINATES = "typed_coordinates"


class FieldState(StrEnum):
    SUPPORTED = "supported"
    UNCERTAIN = "uncertain"
    UNKNOWN = "unknown"
    CONFLICTING = "conflicting"


class UnknownReason(StrEnum):
    NOT_MENTIONED = "not_mentioned"
    UNREADABLE = "unreadable"


class Assertion(StrEnum):
    STATED = "stated"
    HEDGED = "hedged"


class CandidateMethod(StrEnum):
    AI_TEXT = "ai_text"
    AI_IMAGE = "ai_image"
    DIRECT_INPUT = "direct_input"
    HUMAN = "human"
    SENSOR = "sensor"


class EvidenceSource(StrEnum):
    SOS_TEXT = "sos_text"
    SOS_IMAGE = "sos_image"
    SUBMITTED_GPS = "submitted_gps"
    DISPATCHER_INPUT = "dispatcher_input"
    REPORTER_FOLLOWUP = "reporter_followup"
    RESPONDER_OBSERVATION = "responder_observation"
    SENSOR_DATA = "sensor_data"


class ExtractorKind(StrEnum):
    AI = "ai"
    RULE_BASED = "rule_based"


class Modality(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    GPS = "gps"


class ReviewState(StrEnum):
    ACCEPTED_AS_REPORTED = "accepted_as_reported"
    CORRECTED_BY_DISPATCHER = "corrected_by_dispatcher"
    EXTERNALLY_CONFIRMED = "externally_confirmed"
    UNRESOLVED = "unresolved"
    NOT_REVIEWED = "not_reviewed"


class OverallReviewState(StrEnum):
    PARTIAL = "partial"
    COMPLETE = "complete"


class LocationQuality(StrEnum):
    EXTERNALLY_CONFIRMED_POINT = "externally_confirmed_point"
    REPORTED_POINT = "reported_point"
    APPROXIMATE_AREA = "approximate_area"
    LANDMARK_ONLY = "landmark_only"
    UNRESOLVED = "unresolved"
    CONFLICTING = "conflicting"


class AssessmentStage(StrEnum):
    EXTRACTED = "extracted"
    VALIDATED = "validated"


class SuggestedAttention(StrEnum):
    IMMEDIATE_REVIEW = "immediate_review"
    ELEVATED_REVIEW = "elevated_review"
    STANDARD_REVIEW = "standard_review"
    INSUFFICIENT_INFORMATION = "insufficient_information"
