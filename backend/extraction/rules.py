"""Small, disclosed, offline phrase extractor for the competition demo.

Only the phrases in ``_TEXT_RULES`` are recognized. A match is a reported claim,
not a verified fact. Unrecognized text stays UNKNOWN, and no confidence score is
invented. Exact Unicode offsets point back to the unchanged SOS message.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TypeAlias

from backend.domain.sos import (
    AssistanceTag,
    CountRange,
    Evidence,
    EvidenceSource,
    ExtractedFacts,
    ExtractedSOS,
    ExtractorInfo,
    ExtractorKind,
    FieldState,
    LocationCandidate,
    LocationPrecision,
    LocationRole,
    Modality,
    SOSInput,
    TextSpan,
    UnknownReason,
    UrgentSign,
    WaterTag,
    validate_extraction_against_input,
)

_FACT_NAMES = tuple(ExtractedFacts.model_fields)
_COUNT = re.compile(r"(?<!\w)(\d{1,3})\s*(?:people|persons|người)(?!\w)", re.IGNORECASE)


@dataclass(frozen=True)
class _TextRule:
    field: str
    pattern: str
    value: object
    negation: bool = False


# Keep this vocabulary narrow and versioned. Longer negations are collected first,
# then positive matches inside their spans are suppressed.
_TEXT_RULES: tuple[_TextRule, ...] = (
    _TextRule(
        "injury_reported",
        r"\bno one (?:is )?injured\b|\bnot injured\b|không ai bị thương|không bị thương",
        False,
        True,
    ),
    _TextRule(
        "trapped",
        (
            r"\bno one (?:is )?injured or trapped\b|\bno one (?:is )?trapped\b|"
            r"\bnot trapped\b|không ai bị thương hay mắc kẹt|"
            r"không (?:có ai |ai |bị )?mắc kẹt"
        ),
        False,
        True,
    ),
    _TextRule("injury_reported", r"\binjured\b|bị thương", True),
    _TextRule("trapped", r"\btrapped\b|\bstuck\b|mắc kẹt|không thể rời đi", True),
    _TextRule("urgent_signs", r"\bunconscious\b|bất tỉnh", (UrgentSign.UNCONSCIOUS_REPORTED,)),
    _TextRule(
        "urgent_signs",
        r"\bcan(?:not|'t) breathe\b|\bdifficulty breathing\b|khó thở",
        (UrgentSign.BREATHING_DIFFICULTY_REPORTED,),
    ),
    _TextRule(
        "urgent_signs",
        r"\bsevere bleeding\b|chảy máu nghiêm trọng",
        (UrgentSign.SEVERE_BLEEDING_REPORTED,),
    ),
    _TextRule(
        "water_signals", r"\brising water\b|nước đang dâng", (WaterTag.WATER_RISING_REPORTED,)
    ),
    _TextRule(
        "requested_assistance", r"\bneed (?:a )?boat\b|cần xuồng|cần thuyền", (AssistanceTag.BOAT,)
    ),
)

# A narrow denial only suppresses a positive match; it does not prove a whole
# tag category empty or turn a non-request into a request.
_SUPPRESSION_ONLY: tuple[tuple[str, str], ...] = (
    ("urgent_signs", r"\bnot unconscious\b|\bno one (?:is )?unconscious\b|không ai bất tỉnh"),
    ("urgent_signs", r"\bno difficulty breathing\b|không khó thở"),
    ("urgent_signs", r"\bno severe bleeding\b|không chảy máu nghiêm trọng"),
    ("requested_assistance", r"\b(?:do not|don't) need (?:a )?boat\b|không cần (?:xuồng|thuyền)"),
    ("water_signals", r"\bno rising water\b|\bnot rising water\b|không có nước đang dâng"),
)

_HEDGED_URGENT = re.compile(
    r"hình như(?: một người)? (?:ngất|bất tỉnh)|\bmaybe unconscious\b", re.IGNORECASE
)
_VAGUE_COUNT = re.compile(r"vài người|\ba few people\b|\bsome people\b", re.IGNORECASE)


@dataclass(frozen=True)
class _Claim:
    field: str
    value: object | None
    evidence_id: str
    start: int
    end: int
    hedged: bool = False


_Claims: TypeAlias = dict[str, list[_Claim]]


def _overlaps(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(start < other_end and other_start < end for other_start, other_end in spans)


def _is_hedged(text: str, start: int) -> bool:
    """A small clause-local hedge vocabulary; uncertain rather than asserted."""
    prefix = text[max(0, start - 48) : start]
    clause = re.split(r"[.!?;\n]", prefix)[-1]
    return (
        re.search(r"\b(?:maybe|possibly|perhaps)\b|hình như|có lẽ|khoảng", clause, re.IGNORECASE)
        is not None
    )


def _is_negated_count(text: str, start: int) -> bool:
    prefix = text[max(0, start - 16) : start]
    return re.search(r"(?:\bnot|không phải)\s*$", prefix, re.IGNORECASE) is not None


def _claim_payload(claim: _Claim) -> dict[str, object]:
    return {
        "value": claim.value,
        "assertion": "hedged" if claim.hedged else "stated",
        "method": "direct_input",
        "evidence_ids": (claim.evidence_id,),
        "confidence": None,
    }


def _field_payload(field_name: str, claims: list[_Claim]) -> dict[str, object]:
    if not claims:
        return {
            "state": FieldState.UNKNOWN,
            "value": None,
            "candidates": (),
            "confidence": None,
            "unknown_reason": UnknownReason.NOT_MENTIONED,
        }

    candidates = tuple(_claim_payload(claim) for claim in claims)
    if any(claim.hedged for claim in claims):
        return {
            "state": FieldState.UNCERTAIN,
            "value": None,
            "candidates": candidates,
            "confidence": None,
            "unknown_reason": None,
        }

    values = [claim.value for claim in claims]
    if field_name in {"people_count", "injury_reported", "trapped"}:
        if any(value != values[0] for value in values[1:]):
            return {
                "state": FieldState.CONFLICTING,
                "value": None,
                "candidates": candidates,
                "confidence": None,
                "unknown_reason": None,
            }
        value: object = values[0]
    else:
        if any(value == () for value in values) and any(value != () for value in values):
            return {
                "state": FieldState.CONFLICTING,
                "value": None,
                "candidates": candidates,
                "confidence": None,
                "unknown_reason": None,
            }
        tags: list[object] = []
        for item in values:
            if isinstance(item, tuple):
                tags.extend(tag for tag in item if tag not in tags)
        value = tuple(tags)
    return {
        "state": FieldState.SUPPORTED,
        "value": value,
        "candidates": candidates,
        "confidence": None,
        "unknown_reason": None,
    }


class RuleBasedSOSExtractor:
    """Extract only a limited English/Vietnamese demo vocabulary offline."""

    version = "demo-phrase-rules-v1"

    def extract(self, report: SOSInput) -> ExtractedSOS:
        claims: _Claims = {name: [] for name in _FACT_NAMES}
        evidence: list[Evidence] = []
        text = report.raw_text

        def add_text(
            field: str, value: object | None, start: int, end: int, *, hedged: bool = False
        ) -> None:
            evidence_id = f"text-{len(evidence) + 1}"
            evidence.append(
                Evidence(
                    id=evidence_id,
                    source_type=EvidenceSource.SOS_TEXT,
                    source_ref=report.sos_id,
                    text_span=TextSpan(start=start, end=end, quote=text[start:end]),
                    image_box=None,
                    captured_at=None,
                )
            )
            claims[field].append(_Claim(field, value, evidence_id, start, end, hedged))

        # Keep negation and hedging spans out of the positive matching pass.
        suppressed: dict[str, list[tuple[int, int]]] = {name: [] for name in _FACT_NAMES}
        for rule in _TEXT_RULES:
            if not rule.negation:
                continue
            for match in re.finditer(rule.pattern, text, re.IGNORECASE):
                if _overlaps(match.start(), match.end(), suppressed[rule.field]):
                    continue
                add_text(rule.field, rule.value, match.start(), match.end())
                suppressed[rule.field].append((match.start(), match.end()))

        for field, pattern in _SUPPRESSION_ONLY:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                suppressed[field].append((match.start(), match.end()))
        for match in _HEDGED_URGENT.finditer(text):
            add_text(
                "urgent_signs",
                (UrgentSign.UNCONSCIOUS_REPORTED,),
                match.start(),
                match.end(),
                hedged=True,
            )
            suppressed["urgent_signs"].append((match.start(), match.end()))

        for match in _COUNT.finditer(text):
            if _is_negated_count(text, match.start()):
                continue
            number = int(match.group(1))
            add_text(
                "people_count",
                CountRange(min=number, max=number),
                match.start(),
                match.end(),
                hedged=_is_hedged(text, match.start()),
            )
        for match in _VAGUE_COUNT.finditer(text):
            if not _overlaps(match.start(), match.end(), suppressed["people_count"]):
                add_text("people_count", None, match.start(), match.end(), hedged=True)

        for rule in _TEXT_RULES:
            if rule.negation:
                continue
            for match in re.finditer(rule.pattern, text, re.IGNORECASE):
                if _overlaps(match.start(), match.end(), suppressed[rule.field]):
                    continue
                add_text(
                    rule.field,
                    rule.value,
                    match.start(),
                    match.end(),
                    hedged=_is_hedged(text, match.start()),
                )

        fact_payloads = {name: _field_payload(name, claims[name]) for name in _FACT_NAMES}
        modalities = [Modality.TEXT]
        submitted = report.submitted_location
        if submitted is not None and submitted.role is LocationRole.INCIDENT:
            evidence_id = f"gps-{len(evidence) + 1}"
            evidence.append(
                Evidence(
                    id=evidence_id,
                    source_type=EvidenceSource.SUBMITTED_GPS,
                    source_ref=report.sos_id,
                    text_span=None,
                    image_box=None,
                    captured_at=submitted.captured_at,
                )
            )
            point = LocationCandidate(
                description=None, point=submitted.point, precision=LocationPrecision.POINT
            )
            fact_payloads["incident_location"] = {
                "state": FieldState.SUPPORTED,
                "value": point,
                "candidates": (
                    {
                        "value": point,
                        "assertion": "stated",
                        "method": "direct_input",
                        "evidence_ids": (evidence_id,),
                        "confidence": None,
                    },
                ),
                "confidence": None,
                "unknown_reason": None,
            }
            modalities.append(Modality.GPS)

        extraction = ExtractedSOS(
            schema_version=report.schema_version,
            extraction_id=f"rules-{report.sos_id}-r{report.input_revision}",
            sos_id=report.sos_id,
            input_revision=report.input_revision,
            extractor=ExtractorInfo(
                kind=ExtractorKind.RULE_BASED,
                model_id=None,
                version=self.version,
                prompt_version=None,
                modalities_used=tuple(modalities),
            ),
            extracted_at=report.received_at,
            evidence=tuple(evidence),
            facts=ExtractedFacts.model_validate(fact_payloads),
        )
        validate_extraction_against_input(extraction, report)
        return extraction
