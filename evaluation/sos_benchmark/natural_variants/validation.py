"""Conservative, offline checks for imported Vietnamese SOS surface variants.

These checks catch mechanical changes and obvious leaks. Passing them is not a
semantic approval: a separate reviewer must still inspect every new variant.
"""

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from backend.domain.sos_types import CountRange, FieldState
from evaluation.sos_benchmark.models import FactPath
from evaluation.sos_benchmark.natural_variants.models import RenderFact, RenderSpec

_NUMBER = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?(?!\w)", re.UNICODE)
_GPS_PAIR = re.compile(r"(?<!\d)-?\d{1,3}[.,]\d{4,}\s*[,;/ ]\s*-?\d{1,3}[.,]\d{4,}(?!\d)")
_HEDGE = re.compile(
    r"\b(?:hình như|nghe nói|có lẽ|có thể|chưa chắc|không chắc|"
    r"ước|khoảng|vài|mấy|chưa rõ|không rõ)\b"
)
_CONFLICT = re.compile(r"(?:người thứ nhất|người thứ hai|hai nguồn|hai người|mâu thuẫn|khác nhau)")
_ZERO_COUNT = re.compile(r"(?:không có ai|không người nào|không còn ai|0\s*người)")
_INJURY_NEGATION = re.compile(
    r"(?:không ai bị thương|không có người bị thương|chưa ai bị thương|"
    r"chưa có ai bị thương|không người nào bị thương|"
    r"mọi người[^,.]{0,25}không bị thương|không ghi nhận thương tích)"
)
_INJURY_POSITIVE = re.compile(r"(?:bị thương|vết thương|thương tích|chảy máu)")
_DISTRACTOR_CUE = re.compile(
    r"(?:điện thoại|\bpin\b|máy mượn|máy của bạn|máy điện thoại|màn hình|\bsố nhà\b|biển số)"
)
_WRITTEN_COUNT = (
    r"(?:mười(?:\s+(?:một|hai|ba|bốn|năm|sáu|bảy|tám|chín))?|"
    r"hai mươi|một|hai|ba|bốn|năm|sáu|bảy|tám|chín)"
)

_UNKNOWN_CUES: dict[FactPath, re.Pattern[str]] = {
    FactPath.PEOPLE_COUNT: re.compile(
        r"(?:\d+\s*(?:người|nạn nhân)|không có ai|không (?:có|còn) người|"
        r"không người nào|đếm được|mấy người|vài người|một nhóm người|"
        rf"(?:có|còn|gồm|khoảng|ước)\s+(?:khoảng\s+)?{_WRITTEN_COUNT}\s+người)"
    ),
    FactPath.INJURY_REPORTED: _INJURY_POSITIVE,
    FactPath.URGENT_SIGNS: re.compile(r"(?:bất tỉnh|khó thở|thở rất khó|mất ý thức|chảy máu nặng)"),
    FactPath.VULNERABLE_GROUPS: re.compile(
        r"(?:trẻ nhỏ|em bé|người lớn tuổi|cụ già|mang thai|khuyết tật|"
        r"không tự đi|không tự di chuyển|đi lại khó khăn|nhóm cần hỗ trợ đặc biệt)"
    ),
    FactPath.TRAPPED: re.compile(
        r"(?:bị kẹt|mắc kẹt|không ra được|không thoát ra được|chưa thoát ra được)"
    ),
    FactPath.WATER_SIGNALS: re.compile(r"(?:nước[^,.]{0,25}(?:dâng|ngập|lên)|dòng chảy mạnh)"),
    FactPath.FIRE_SIGNALS: re.compile(r"(?:khói|cháy|ngọn lửa)"),
    FactPath.STRUCTURE_SIGNALS: re.compile(r"(?:nứt|sập|đổ nhà|hư hại nhà)"),
    FactPath.ACCESS_OBSERVATIONS: re.compile(
        r"(?:xe (?:không|vẫn|còn)[^,.]{0,25}(?:vào|qua|tới)|"
        r"đường (?:cho xe|xe)[^,.]{0,25}(?:chặn|thông|bít)|lối xe chạy)"
    ),
    FactPath.REQUESTED_ASSISTANCE: re.compile(
        r"(?:(?:xin|cần|mong)[^,.]{0,35}(?:xuồng|thuyền|đội y tế|sơ tán|đưa người ra)|"
        r"hỗ trợ (?:y tế|sơ tán|đưa người ra))"
    ),
    FactPath.INCIDENT_LOCATION: re.compile(
        r"(?:nơi xảy ra sự việc|vị trí sự việc|tọa độ hiện trường|"
        r"gần (?:cầu|bến)|ở (?:đầu|cuối) (?:con ngõ|lối mòn|đường đất))"
    ),
}

_NUMBER_WORDS: dict[int, str] = {
    0: "không",
    1: "một",
    2: "hai",
    3: "ba",
    4: "bốn",
    5: "năm",
    6: "sáu",
    7: "bảy",
    8: "tám",
    9: "chín",
    10: "mười",
}


@dataclass(frozen=True)
class DeterministicResult:
    """Hard failures and issues reserved for independent semantic review."""

    rejection_reasons: tuple[str, ...]
    review_notes: tuple[str, ...]


def normalize_variant_text(value: str) -> str:
    """Stable duplicate key, preserving diacritics while folding case and space."""

    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _number_token(token: str) -> str:
    try:
        return format(Decimal(token.replace(",", ".")).normalize(), "f")
    except InvalidOperation:
        return token


def _required_count_numbers(fact: RenderFact) -> set[int]:
    required: set[int] = set()

    def collect(value: Any) -> None:
        if isinstance(value, CountRange):
            required.add(value.min)
            if value.max is not None:
                required.add(value.max)
        elif isinstance(value, dict):
            if "min" in value and "max" in value:
                required.add(int(value["min"]))
                if value["max"] is not None:
                    required.add(int(value["max"]))
            else:
                for nested in value.values():
                    collect(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                collect(nested)

    collect(fact.value)
    for claim in fact.claims:
        collect(claim.value)
    return required


def _word_for_number(value: int) -> str | None:
    if value in _NUMBER_WORDS:
        return _NUMBER_WORDS[value]
    if 11 <= value <= 19:
        suffix = _NUMBER_WORDS[value - 10]
        return f"mười {suffix}"
    if value == 20:
        return "hai mươi"
    return None


def _count_is_mentioned(value: int, text: str, numeric_tokens: set[str]) -> bool:
    if str(value) in numeric_tokens and re.search(
        rf"(?<!\w){value}(?!\w)\s*(?:(?:đến|tới|-)\s*\d+\s*)?(?:người|nạn nhân)\b",
        text,
    ):
        return True
    if value == 0:
        return _ZERO_COUNT.search(text) is not None
    word = _word_for_number(value)
    return word is not None and re.search(rf"\b{re.escape(word)}\s+người\b", text) is not None


def _vulnerable_count_is_mentioned(value: int, text: str) -> bool:
    noun = r"(?:người|trẻ|em|bé|cụ|thai phụ)"
    if re.search(rf"(?<!\w){value}(?!\w)\s+{noun}\b", text):
        return True
    word = _word_for_number(value)
    return word is not None and re.search(rf"\b{re.escape(word)}\s+{noun}\b", text) is not None


def _allowed_numeric_tokens(spec: RenderSpec) -> set[str]:
    allowed: set[str] = set()
    for fact in spec.allowed_facts.values():
        allowed.update(str(value) for value in _required_count_numbers(fact))
        for claim in fact.claims:
            for quote in claim.source_quotes:
                allowed.update(_number_token(match.group()) for match in _NUMBER.finditer(quote))
    for phrase in spec.allowed_distractors:
        allowed.update(_number_token(match.group()) for match in _NUMBER.finditer(phrase))
    return allowed


def _has_unapproved_distractor(text: str, spec: RenderSpec) -> bool:
    permitted = normalize_variant_text(text)
    for phrase in spec.allowed_distractors:
        permitted = permitted.replace(normalize_variant_text(phrase), " ")
    return _DISTRACTOR_CUE.search(permitted) is not None


def validate_variant(spec: RenderSpec, text: str) -> DeterministicResult:
    """Reject detectable fact changes without claiming full semantic coverage."""

    if not text.strip():
        return DeterministicResult(("empty_text",), ())

    normalized = normalize_variant_text(text)
    failures: list[str] = []
    notes: list[str] = []
    if _GPS_PAIR.search(normalized):
        failures.append("gps_coordinates_in_text")
    submitted = spec.external_context.submitted_location
    if submitted is not None:
        lat = str(submitted.point.lat)
        lon = str(submitted.point.lon)
        if lat in normalized or lon in normalized:
            failures.append("external_gps_value_in_text")

    present_numeric = {_number_token(match.group()) for match in _NUMBER.finditer(normalized)}
    unexpected = present_numeric - _allowed_numeric_tokens(spec)
    if unexpected:
        failures.append("forbidden_numeric_addition:" + ",".join(sorted(unexpected)))
    count = spec.allowed_facts.get(FactPath.PEOPLE_COUNT)
    if count is not None:
        for expected in sorted(_required_count_numbers(count)):
            if not _count_is_mentioned(expected, normalized, present_numeric):
                failures.append(f"missing_required_count:{expected}")
        if count.state is FieldState.CONFLICTING and len(_required_count_numbers(count)) < 2:
            notes.append("conflicting_count_requires_semantic_review")
        if count.state is FieldState.SUPPORTED and isinstance(count.value, CountRange):
            if count.value.min == count.value.max == 0 and not _ZERO_COUNT.search(normalized):
                notes.append("zero_count_needs_explicit_denial_review")
    vulnerable = spec.allowed_facts.get(FactPath.VULNERABLE_GROUPS)
    if vulnerable is not None:
        for expected in sorted(_required_count_numbers(vulnerable)):
            if not _vulnerable_count_is_mentioned(expected, normalized):
                failures.append(f"missing_required_numeric:vulnerable_groups:{expected}")

    for path in spec.must_remain_unknown:
        cue = _UNKNOWN_CUES[path]
        if cue.search(normalized):
            failures.append(f"unknown_field_cue:{path.value}")
    if FactPath.INCIDENT_LOCATION in spec.external_context.held_out_facts:
        if _UNKNOWN_CUES[FactPath.INCIDENT_LOCATION].search(normalized):
            failures.append("held_out_location_cue")

    injury = spec.allowed_facts.get(FactPath.INJURY_REPORTED)
    if injury is not None and injury.state is FieldState.SUPPORTED and injury.value is False:
        if not _INJURY_NEGATION.search(normalized):
            notes.append("supported_false_injury_requires_denial_review")
        without_negation = _INJURY_NEGATION.sub(" ", normalized)
        if _INJURY_POSITIVE.search(without_negation):
            failures.append("positive_injury_contradicts_false")
    if injury is not None and injury.state is FieldState.SUPPORTED and injury.value is True:
        if not _INJURY_POSITIVE.search(normalized):
            notes.append("positive_injury_not_detected")

    if any(fact.state is FieldState.CONFLICTING for fact in spec.allowed_facts.values()):
        if not _CONFLICT.search(normalized):
            notes.append("conflict_attribution_not_detected")
    if any(fact.state is FieldState.UNCERTAIN for fact in spec.allowed_facts.values()):
        if not _HEDGE.search(normalized):
            notes.append("hedge_not_detected")
    if _has_unapproved_distractor(text, spec):
        failures.append("unauthorized_distractor")
    return DeterministicResult(tuple(dict.fromkeys(failures)), tuple(dict.fromkeys(notes)))
