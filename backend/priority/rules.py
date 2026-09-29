"""Provisional, deterministic attention labels for the offline demo.

These discrete weights are an illustrative queue convention, not clinical
triage or an approved dispatch weight. A dispatcher must review each result.
"""

from __future__ import annotations

from backend.domain.sos import (
    AssessmentStage,
    ExtractedSOS,
    FieldState,
    PriorityAssessment,
    Reason,
    SnapshotReference,
    SuggestedAttention,
)


def _supported_positive(field: object) -> bool:
    """Return true only for an explicitly supported positive bool/tag tuple."""
    state = getattr(field, "state")
    value = getattr(field, "value")
    return state is FieldState.SUPPORTED and bool(value)


class RuleBasedPriorityAssessor:
    """Assign a demo review label from supported cues, preserving uncertainty."""

    policy_version = "demo-attention-rules-v1"

    def assess(self, snapshot: ExtractedSOS) -> PriorityAssessment:
        facts = snapshot.facts
        unresolved = tuple(
            name
            for name in type(facts).model_fields
            if getattr(facts, name).state is not FieldState.SUPPORTED
        )
        urgent = _supported_positive(facts.urgent_signs)
        injury = (
            facts.injury_reported.state is FieldState.SUPPORTED
            and facts.injury_reported.value is True
        )
        trapped = facts.trapped.state is FieldState.SUPPORTED and facts.trapped.value is True
        hazard = any(
            _supported_positive(field)
            for field in (facts.water_signals, facts.fire_signals, facts.structure_signals)
        )
        count = (
            facts.people_count.value if facts.people_count.state is FieldState.SUPPORTED else None
        )

        reasons: tuple[Reason, ...]
        if count is not None and count.min == 0 and count.max == 0:
            # A zero-person SOS may conflict with the need for a rescue team.
            attention = SuggestedAttention.INSUFFICIENT_INFORMATION
            weight = None
            unresolved = tuple(sorted(set((*unresolved, "people_count"))))
            reasons = (
                Reason(
                    field_path="facts.people_count",
                    rule_id="zero_people_review",
                    explanation="A reported zero people requires dispatcher clarification.",
                    contribution=None,
                ),
            )
        elif urgent or (trapped and hazard):
            attention = SuggestedAttention.IMMEDIATE_REVIEW
            weight = 5.0
            reason_path = "facts.urgent_signs" if urgent else "facts.trapped"
            reasons = (
                Reason(
                    field_path=reason_path,
                    rule_id="reported_urgent_sign_or_trapped_hazard",
                    explanation=(
                        "A reported urgent sign or trapped status with a reported hazard "
                        "calls for immediate dispatcher review."
                    ),
                    contribution=None,
                ),
            )
        elif injury or trapped or hazard:
            attention = SuggestedAttention.ELEVATED_REVIEW
            supported_cues = sum((injury, trapped, hazard))
            weight = 4.0 if supported_cues >= 2 else 3.0
            reason_path = (
                "facts.injury_reported"
                if injury
                else "facts.trapped"
                if trapped
                else "facts.water_signals"
            )
            reasons = (
                Reason(
                    field_path=reason_path,
                    rule_id="reported_medical_trapped_or_hazard_cue",
                    explanation=(
                        "At least one injury, trapped, or environmental cue was reported; "
                        "the demo weight uses the number of distinct cue categories."
                    ),
                    contribution=None,
                ),
            )
        elif (
            count is not None
            and count.min > 0
            and facts.injury_reported.state is FieldState.SUPPORTED
            and facts.injury_reported.value is False
            and facts.trapped.state is FieldState.SUPPORTED
            and facts.trapped.value is False
        ):
            attention = SuggestedAttention.STANDARD_REVIEW
            weight = 1.0
            reasons = (
                Reason(
                    field_path="facts.people_count",
                    rule_id="reported_people_with_explicit_denials",
                    explanation=(
                        "People were reported and injury and trapping were explicitly denied. "
                        "Other unreported facts remain unresolved."
                    ),
                    contribution=None,
                ),
            )
        else:
            attention = SuggestedAttention.INSUFFICIENT_INFORMATION
            weight = None
            reasons = (
                Reason(
                    field_path="facts",
                    rule_id="insufficient_supported_cues",
                    explanation="The supported claims do not justify a demo priority weight.",
                    contribution=None,
                ),
            )

        assessment = PriorityAssessment(
            schema_version=snapshot.schema_version,
            assessment_id=f"demo-priority-{snapshot.extraction_id}",
            sos_id=snapshot.sos_id,
            based_on=SnapshotReference(
                stage=AssessmentStage.EXTRACTED, snapshot_id=snapshot.extraction_id
            ),
            policy_version=self.policy_version,
            assessed_at=snapshot.extracted_at,
            suggested_attention=attention,
            priority_weight=weight,
            reasons=reasons,
            unresolved_fields=unresolved,
            requires_human_review=True,
        )
        assessment.assert_matches_extraction(snapshot)
        return assessment
