"""Frozen v1 descriptive cue-load rubric; this is not a triage decision."""

from backend.domain.sos_types import AccessStatus, FieldState
from evaluation.sos_benchmark.models import GoldFacts, SignalBucket


def visible_signal_bucket(facts: GoldFacts) -> SignalBucket:
    """Classify only clearly supported cues in the report and supplied metadata."""
    hazard_categories = sum(
        field.state is FieldState.SUPPORTED and bool(field.value)
        for field in (facts.water_signals, facts.fire_signals, facts.structure_signals)
    )
    urgent = facts.urgent_signs.state is FieldState.SUPPORTED and bool(facts.urgent_signs.value)
    trapped = facts.trapped.state is FieldState.SUPPORTED and facts.trapped.value is True
    blocked = facts.access_observations.state is FieldState.SUPPORTED and any(
        item.status is AccessStatus.BLOCKED_REPORTED
        for item in (facts.access_observations.value or ())
    )
    if urgent or (trapped and hazard_categories > 0) or hazard_categories >= 2:
        return SignalBucket.HIGH
    if hazard_categories > 0 or trapped or blocked:
        return SignalBucket.INTERMEDIATE
    return SignalBucket.LOWER
