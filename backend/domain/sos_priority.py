"""Versioned, explainable priority assessment data contract.

No priority rule or score calculation is implemented here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

from pydantic import Field, StrictBool, model_validator

from backend.domain.sos_types import (
    AssessmentStage,
    NonBlankString,
    SchemaVersion,
    SOSModel,
    StrictFiniteFloat,
    SuggestedAttention,
    UTCDateTime,
)

if TYPE_CHECKING:
    from backend.domain.sos_extraction import ExtractedSOS
    from backend.domain.sos_review import ValidatedSOS


class SnapshotReference(SOSModel):
    """Identify the immutable extraction or validation used for an assessment."""

    stage: AssessmentStage
    snapshot_id: NonBlankString


class Reason(SOSModel):
    """Explain one versioned policy rule's contribution."""

    field_path: NonBlankString
    rule_id: NonBlankString
    explanation: NonBlankString
    contribution: StrictFiniteFloat | None = Field(...)


class PriorityAssessment(SOSModel):
    """A suggested attention level, separate from dispatch approval."""

    schema_version: SchemaVersion
    assessment_id: NonBlankString
    sos_id: NonBlankString
    based_on: SnapshotReference
    policy_version: NonBlankString
    assessed_at: UTCDateTime
    suggested_attention: SuggestedAttention
    priority_weight: StrictFiniteFloat | None = Field(..., gt=0)
    reasons: tuple[Reason, ...]
    unresolved_fields: tuple[NonBlankString, ...]
    requires_human_review: StrictBool

    @model_validator(mode="after")
    def check_recommendation(self) -> Self:
        if self.based_on.stage.value == "extracted" and not self.requires_human_review:
            raise ValueError("an extracted-data assessment is provisional and requires review")
        if self.suggested_attention.value == "insufficient_information":
            if self.priority_weight is not None:
                raise ValueError("insufficient information cannot produce a priority weight")
            if not self.requires_human_review:
                raise ValueError("insufficient information requires human review")
        elif not self.reasons:
            raise ValueError("an attention recommendation requires an explained policy rule")
        return self

    def assert_matches_extraction(self, extraction: ExtractedSOS) -> None:
        """Verify the referenced extraction when that snapshot is available."""

        if self.based_on.stage.value != "extracted":
            raise ValueError("assessment does not reference an extraction")
        if (
            self.sos_id != extraction.sos_id
            or self.based_on.snapshot_id != extraction.extraction_id
        ):
            raise ValueError("assessment must reference its source extraction")
        if self.schema_version != extraction.schema_version:
            raise ValueError("assessment and extraction schema versions must match")

    def assert_matches_validation(self, validation: ValidatedSOS) -> None:
        """Verify the referenced review when that snapshot is available."""

        if self.based_on.stage.value != "validated":
            raise ValueError("assessment does not reference a validation")
        if (
            self.sos_id != validation.sos_id
            or self.based_on.snapshot_id != validation.validation_id
        ):
            raise ValueError("assessment must reference its source validation")
        if self.schema_version != validation.schema_version:
            raise ValueError("assessment and validation schema versions must match")
