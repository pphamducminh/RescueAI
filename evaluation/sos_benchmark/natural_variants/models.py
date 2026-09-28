"""Provider-independent, report-visible instructions for SOS text variation.

The provider payload deliberately excludes submitted GPS metadata. A render
spec is an instruction set for language variation, not a new gold annotation.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, JsonValue, model_validator

from backend.domain.sos_extraction import SubmittedLocation
from backend.domain.sos_types import Assertion, FieldState, LocationRole, NonBlankString, SOSModel
from evaluation.sos_benchmark.models import FactPath, StyleFlag
from evaluation.sos_benchmark.templates import PHRASES


class RenderClaim(SOSModel):
    """A reported claim, with the source wording available to a paraphraser."""

    value: JsonValue = Field(...)
    assertion: Assertion
    witness_id: NonBlankString
    scope_id: NonBlankString
    time_ref: NonBlankString
    source_quotes: tuple[NonBlankString, ...]


class RenderFact(SOSModel):
    """The report-visible state and values of one non-unknown SOS field."""

    state: FieldState
    value: JsonValue = Field(...)
    claims: tuple[RenderClaim, ...]

    @model_validator(mode="after")
    def check_state(self) -> Self:
        if self.state is FieldState.UNKNOWN:
            raise ValueError("unknown fields belong in must_remain_unknown")
        if not self.claims:
            raise ValueError("a non-unknown fact requires at least one claim")
        if self.state is FieldState.SUPPORTED:
            if self.value is None or not any(
                claim.assertion is Assertion.STATED for claim in self.claims
            ):
                raise ValueError("supported facts require a value and stated claim")
        elif self.value is not None:
            raise ValueError("uncertain and conflicting fact values must be null")
        if self.state is FieldState.UNCERTAIN and not any(
            claim.assertion is Assertion.HEDGED for claim in self.claims
        ):
            raise ValueError("uncertain facts require a hedged claim")
        if self.state is FieldState.CONFLICTING and (
            len(self.claims) < 2
            or len({claim.witness_id for claim in self.claims}) < 2
        ):
            raise ValueError("conflicting facts require distinct witness claims")
        return self


class ExternalContext(SOSModel):
    """Metadata retained outside the provider-facing text instructions."""

    submitted_location: SubmittedLocation | None = Field(...)
    held_out_facts: dict[FactPath, RenderFact]

    @model_validator(mode="after")
    def check_held_out_fields(self) -> Self:
        if any(field is not FactPath.INCIDENT_LOCATION for field in self.held_out_facts):
            raise ValueError("only GPS-backed incident location may be held out")
        if self.held_out_facts:
            submitted = self.submitted_location
            if submitted is None or submitted.role is not LocationRole.INCIDENT:
                raise ValueError("held-out incident point needs an incident-role submission")
            location = self.held_out_facts[FactPath.INCIDENT_LOCATION]
            value = location.value
            if (
                location.state is not FieldState.SUPPORTED
                or not isinstance(value, dict)
                or value.get("precision") != "point"
                or value.get("point") != submitted.point.model_dump(mode="json")
                or any(claim.source_quotes for claim in location.claims)
            ):
                raise ValueError("held-out location must be the submitted incident GPS only")
        return self


class RenderStyle(SOSModel):
    language: Literal["vi"]
    preferred_noise: tuple[StyleFlag, ...]

    @model_validator(mode="after")
    def check_noise(self) -> Self:
        if len(set(self.preferred_noise)) != len(self.preferred_noise):
            raise ValueError("preferred noise flags must be unique")
        return self


class RenderSpec(SOSModel):
    """A complete, field-partitioned instruction set for one Task 5 case."""

    case_id: NonBlankString
    allowed_facts: dict[FactPath, RenderFact]
    must_remain_unknown: tuple[FactPath, ...]
    external_context: ExternalContext
    allowed_distractors: tuple[NonBlankString, ...]
    style: RenderStyle

    @model_validator(mode="after")
    def check_partition_and_sources(self) -> Self:
        allowed = set(self.allowed_facts)
        unknown = set(self.must_remain_unknown)
        held_out = set(self.external_context.held_out_facts)
        if len(unknown) != len(self.must_remain_unknown):
            raise ValueError("must_remain_unknown must not contain duplicates")
        if allowed & unknown or allowed & held_out or unknown & held_out:
            raise ValueError("SOS facts must appear in exactly one render-spec section")
        if allowed | unknown | held_out != set(FactPath):
            raise ValueError("the render spec must account for all eleven SOS facts")
        if len(self.allowed_distractors) != len(set(self.allowed_distractors)):
            raise ValueError("allowed distractors must be unique")
        vetted = {phrase for bank in PHRASES.values() for phrase in bank["irrelevant"]}
        if not set(self.allowed_distractors).issubset(vetted):
            raise ValueError("distractors must come from the vetted irrelevant phrase bank")
        if self.allowed_distractors and StyleFlag.IRRELEVANT not in self.style.preferred_noise:
            raise ValueError("distractors require the irrelevant style flag")
        for field, fact in self.allowed_facts.items():
            if any(not claim.source_quotes for claim in fact.claims):
                raise ValueError(f"{field.value} needs report-text source quotes")
        return self

    def provider_payload(self) -> dict[str, object]:
        """Return only language-generation instructions, never external GPS."""
        return {
            "case_id": self.case_id,
            "allowed_facts": {
                field.value: fact.model_dump(mode="json")
                for field, fact in self.allowed_facts.items()
            },
            "must_remain_unknown": [field.value for field in self.must_remain_unknown],
            "allowed_distractors": list(self.allowed_distractors),
            "style": self.style.model_dump(mode="json"),
        }
