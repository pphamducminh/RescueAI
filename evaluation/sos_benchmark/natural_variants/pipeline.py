"""Offline import, triage, and storage for externally produced SOS variants."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path
from typing import Protocol, Self

from pydantic import Field, StrictInt, StrictStr, model_validator

from backend.domain.sos_types import NonBlankString, SOSModel
from evaluation.sos_benchmark.models import BenchmarkRecord

from .models import RenderSpec
from .specs import build_render_spec
from .validation import normalize_variant_text, validate_variant

DEFAULT_VARIANT_DIR = Path("data/sos_benchmark/natural_variants")


class VariantStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    NEEDS_SEMANTIC_REVIEW = "NEEDS_SEMANTIC_REVIEW"


class SemanticVerdict(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    UNRESOLVED = "UNRESOLVED"


class VariantInput(SOSModel):
    """One provider-independent external text candidate."""

    text: StrictStr
    noise_type: NonBlankString
    variant_id: NonBlankString | None = Field(default=None)


class VariantBatch(SOSModel):
    """A batch must name one frozen Task 5 case."""

    case_id: NonBlankString
    variants: tuple[VariantInput, ...] = Field(min_length=1)
    source_id: NonBlankString | None = Field(default=None)


class SemanticReviewDecision(SOSModel):
    """Explicit decision from an independent future reviewer."""

    verdict: SemanticVerdict
    reviewer_id: NonBlankString
    rationale: NonBlankString


class SemanticValidator(Protocol):
    """Inject a reviewed semantic gate; no model/provider implementation is bundled."""

    def review(self, spec: RenderSpec, variant: VariantInput) -> SemanticReviewDecision: ...


class VariantResult(SOSModel):
    """Audit record; no Task 5 labels or latent facts are rewritten here."""

    source_line: StrictInt = Field(ge=1)
    case_id: StrictStr | None = Field(...)
    source_id: StrictStr | None = Field(...)
    variant_id: StrictStr | None = Field(...)
    variant_index: StrictInt | None = Field(...)
    text: StrictStr | None = Field(...)
    noise_type: StrictStr | None = Field(...)
    status: VariantStatus
    reasons: tuple[NonBlankString, ...]
    semantic_review: SemanticReviewDecision | None = Field(...)

    @model_validator(mode="after")
    def check_status(self) -> Self:
        if self.status is VariantStatus.ACCEPTED and (
            self.semantic_review is None
            or self.semantic_review.verdict is not SemanticVerdict.APPROVED
            or self.reasons
        ):
            raise ValueError("acceptance requires explicit semantic approval and no rejections")
        if self.status is VariantStatus.REJECTED and not self.reasons:
            raise ValueError("rejected variants need reasons")
        return self


class ImportSummary(SOSModel):
    raw_batches: StrictInt = Field(ge=0)
    accepted: StrictInt = Field(ge=0)
    rejected: StrictInt = Field(ge=0)
    needs_review: StrictInt = Field(ge=0)


def _stable_id(case_id: str, normalized_text: str) -> str:
    digest = hashlib.sha256(f"{case_id}\0{normalized_text}".encode()).hexdigest()[:20]
    return f"variant-{digest}"


def _result(
    *,
    source_line: int,
    case_id: str | None,
    source_id: str | None,
    variant_id: str | None,
    variant_index: int | None,
    text: str | None,
    noise_type: str | None,
    status: VariantStatus,
    reasons: tuple[str, ...],
    semantic_review: SemanticReviewDecision | None = None,
) -> VariantResult:
    return VariantResult(
        source_line=source_line,
        case_id=case_id,
        source_id=source_id,
        variant_id=variant_id,
        variant_index=variant_index,
        text=text,
        noise_type=noise_type,
        status=status,
        reasons=reasons,
        semantic_review=semantic_review,
    )


def _parse_batch(line: bytes) -> VariantBatch:
    decoded = line.decode("utf-8")
    return VariantBatch.model_validate_json(decoded)


def _invalid_batch_context(line: bytes) -> tuple[str | None, str | None]:
    try:
        data = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    case_id = data.get("case_id")
    source_id = data.get("source_id")
    return (
        case_id if isinstance(case_id, str) else None,
        source_id if isinstance(source_id, str) else None,
    )


def _jsonl_bytes(records: Iterable[VariantResult]) -> bytes:
    return b"".join(
        (
            json.dumps(
                record.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        for record in records
    )


def import_variants(
    input_path: str | Path,
    cases: Iterable[BenchmarkRecord],
    output_dir: str | Path = DEFAULT_VARIANT_DIR,
    semantic_validator: SemanticValidator | None = None,
) -> ImportSummary:
    """Import raw batches, reject detectable errors, and queue semantic review.

    Raw bytes are preserved for audit even if a line is malformed JSON. The
    three classified outputs are always valid, deterministic JSONL. Without an
    explicit semantic validator, no candidate can be accepted automatically.
    """

    specs: dict[str, RenderSpec] = {}
    for case in cases:
        if case.case_id in specs:
            raise ValueError(f"duplicate Task 5 case_id {case.case_id!r}")
        specs[case.case_id] = build_render_spec(case)

    raw = Path(input_path).read_bytes()
    destination = Path(output_dir)

    classified: dict[VariantStatus, list[VariantResult]] = {
        status: [] for status in VariantStatus
    }
    seen_sources: set[str] = set()
    seen_variant_ids: set[str] = set()
    seen_texts: set[tuple[str, str]] = set()
    lines = raw.splitlines()
    for source_line, line in enumerate(lines, 1):
        try:
            batch = _parse_batch(line)
        except (UnicodeDecodeError, ValueError):
            case_id, source_id = _invalid_batch_context(line)
            classified[VariantStatus.REJECTED].append(
                _result(
                    source_line=source_line,
                    case_id=case_id,
                    source_id=source_id,
                    variant_id=None,
                    variant_index=None,
                    text=None,
                    noise_type=None,
                    status=VariantStatus.REJECTED,
                    reasons=("invalid_batch_json_or_schema",),
                )
            )
            continue

        duplicate_source = batch.source_id is not None and batch.source_id in seen_sources
        if batch.source_id is not None:
            seen_sources.add(batch.source_id)
        spec = specs.get(batch.case_id)
        for variant_index, variant in enumerate(batch.variants):
            normalized = normalize_variant_text(variant.text)
            variant_id = variant.variant_id or _stable_id(batch.case_id, normalized)
            reasons: list[str] = []
            if spec is None:
                reasons.append("unknown_case_id")
            if duplicate_source:
                reasons.append("duplicate_source_id")
            if variant_id in seen_variant_ids:
                reasons.append("duplicate_variant_id")
            seen_variant_ids.add(variant_id)
            text_key = (batch.case_id, normalized)
            if text_key in seen_texts:
                reasons.append("duplicate_variant_text")
            seen_texts.add(text_key)
            if spec is not None:
                checks = validate_variant(spec, variant.text)
                reasons.extend(checks.rejection_reasons)
            if reasons:
                classified[VariantStatus.REJECTED].append(
                    _result(
                        source_line=source_line,
                        case_id=batch.case_id,
                        source_id=batch.source_id,
                        variant_id=variant_id,
                        variant_index=variant_index,
                        text=variant.text,
                        noise_type=variant.noise_type,
                        status=VariantStatus.REJECTED,
                        reasons=tuple(dict.fromkeys(reasons)),
                    )
                )
                continue
            assert spec is not None
            decision = semantic_validator.review(spec, variant) if semantic_validator else None
            if decision is not None and decision.verdict is SemanticVerdict.REJECTED:
                status = VariantStatus.REJECTED
                result_reasons: tuple[str, ...] = ("semantic_review_rejected",)
            elif decision is not None and decision.verdict is SemanticVerdict.APPROVED:
                status = VariantStatus.ACCEPTED
                result_reasons = ()
            else:
                status = VariantStatus.NEEDS_SEMANTIC_REVIEW
                result_reasons = checks.review_notes
            classified[status].append(
                _result(
                    source_line=source_line,
                    case_id=batch.case_id,
                    source_id=batch.source_id,
                    variant_id=variant_id,
                    variant_index=variant_index,
                    text=variant.text,
                    noise_type=variant.noise_type,
                    status=status,
                    reasons=result_reasons,
                    semantic_review=decision,
                )
            )

    destination.mkdir(parents=True, exist_ok=True)
    (destination / "generated_raw.jsonl").write_bytes(raw)
    for status, filename in (
        (VariantStatus.ACCEPTED, "accepted.jsonl"),
        (VariantStatus.REJECTED, "rejected.jsonl"),
        (VariantStatus.NEEDS_SEMANTIC_REVIEW, "needs_review.jsonl"),
    ):
        (destination / filename).write_bytes(_jsonl_bytes(classified[status]))
    return ImportSummary(
        raw_batches=len(lines),
        accepted=len(classified[VariantStatus.ACCEPTED]),
        rejected=len(classified[VariantStatus.REJECTED]),
        needs_review=len(classified[VariantStatus.NEEDS_SEMANTIC_REVIEW]),
    )
