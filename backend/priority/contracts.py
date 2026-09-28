"""Contract for explainable, reviewable priority recommendations."""

from typing import Protocol

from backend.domain.sos import ExtractedSOS, PriorityAssessment, ValidatedSOS


class PriorityAssessor(Protocol):
    """Suggest a priority from a report and its extracted fields."""

    def assess(self, snapshot: ExtractedSOS | ValidatedSOS) -> PriorityAssessment: ...
