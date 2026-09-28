"""Contract for future text extractors."""

from typing import Protocol

from backend.domain.sos import ExtractedSOS, SOSInput


class SOSExtractor(Protocol):
    """Extract structured fields while preserving provenance in the result."""

    def extract(self, report: SOSInput) -> ExtractedSOS: ...
