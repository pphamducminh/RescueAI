"""Build and export safe render specifications from frozen Task 5 records."""

from __future__ import annotations

import json
from collections.abc import Iterable
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from backend.domain.sos_types import EvidenceSource, FieldState
from evaluation.sos_benchmark.models import BenchmarkRecord, FactPath, GoldField, Split, StyleFlag
from evaluation.sos_benchmark.templates import PHRASES

from .models import ExternalContext, RenderClaim, RenderFact, RenderSpec, RenderStyle

DEFAULT_RENDER_SPECS_PATH = Path("data/sos_benchmark/natural_variants/render_specs.jsonl")


def _vetted_distractors(split: Split) -> tuple[str, ...]:
    """Keep Task 5's train/dev/test phrase-bank separation intact."""
    return tuple(PHRASES[split.value]["irrelevant"])


def _render_fact(
    field: GoldField[Any], evidence_by_id: dict[str, Any]
) -> RenderFact:
    claims: list[RenderClaim] = []
    for claim in field.claims:
        quotes: list[str] = []
        for evidence_id in claim.evidence_ids:
            evidence = evidence_by_id[evidence_id]
            if evidence.source_type is EvidenceSource.SOS_TEXT:
                if evidence.text_span is None:
                    raise ValueError(f"text evidence {evidence_id!r} has no span")
                quotes.append(evidence.text_span.quote)
        claims.append(
            RenderClaim(
                value=_json_value(claim.value),
                assertion=claim.assertion,
                witness_id=claim.witness_id,
                scope_id=claim.scope_id,
                time_ref=claim.time_ref,
                source_quotes=tuple(quotes),
            )
        )
    return RenderFact(
        state=field.state,
        value=_json_value(field.value),
        claims=tuple(claims),
    )


def _json_value(value: object) -> Any:
    """Convert validated gold values to JSON types without changing semantics."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value


def build_render_spec(case: BenchmarkRecord) -> RenderSpec:
    """Project report-visible gold to an LLM-neutral, GPS-isolated spec.

    This function intentionally never reads ``latent_scenario``, ``generation``
    metadata, or the original raw text. Source quotes come from validated gold
    evidence only. ``case.split`` selects a vetted distractor bank, never facts.
    Nothing here may redefine the Task 5 labels.
    """
    plan = case.ground_truth.communication_plan
    gold = case.ground_truth.gold_annotation
    submitted_location = case.generated_input.submitted_location
    evidence_by_id = {item.id: item for item in gold.evidence}

    allowed: dict[FactPath, RenderFact] = {}
    held_out: dict[FactPath, RenderFact] = {}
    unknown: list[FactPath] = []
    omitted = set(plan.omitted_fields)
    mentioned = set(plan.mentioned_fields)
    for field_name in FactPath:
        gold_field = getattr(gold.facts, field_name.value)
        if field_name in omitted:
            if gold_field.state is not FieldState.UNKNOWN:
                raise ValueError(f"omitted field {field_name.value} is not unknown in gold")
            unknown.append(field_name)
            continue
        if field_name not in mentioned or gold_field.state is FieldState.UNKNOWN:
            raise ValueError(f"mentioned field {field_name.value} has no report-visible gold")
        rendered_fact = _render_fact(gold_field, evidence_by_id)
        if field_name is FactPath.INCIDENT_LOCATION and any(
            evidence_by_id[evidence_id].source_type is EvidenceSource.SUBMITTED_GPS
            for claim in gold_field.claims
            for evidence_id in claim.evidence_ids
        ):
            held_out[field_name] = rendered_fact
        else:
            allowed[field_name] = rendered_fact

    preferred_noise = tuple(StyleFlag(flag) for flag in plan.noise_edits)
    distractors = _vetted_distractors(case.split) if StyleFlag.IRRELEVANT in preferred_noise else ()
    return RenderSpec(
        case_id=case.case_id,
        allowed_facts=allowed,
        must_remain_unknown=tuple(unknown),
        external_context=ExternalContext(
            submitted_location=submitted_location,
            held_out_facts=held_out,
        ),
        allowed_distractors=distractors,
        style=RenderStyle(language="vi", preferred_noise=preferred_noise),
    )


def load_benchmark_cases(path: str | Path) -> list[BenchmarkRecord]:
    """Read and schema-validate private Task 5 JSONL, rejecting duplicate IDs."""
    source = Path(path)
    records: list[BenchmarkRecord] = []
    seen: set[str] = set()
    with source.open("r", encoding="utf-8", newline="") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                raise ValueError(f"{source}:{line_number}: blank JSONL line")
            try:
                record = BenchmarkRecord.model_validate_json(line)
            except ValueError as exc:
                raise ValueError(f"{source}:{line_number}: invalid Task 5 case: {exc}") from exc
            if record.case_id in seen:
                raise ValueError(f"{source}:{line_number}: duplicate case_id {record.case_id!r}")
            seen.add(record.case_id)
            records.append(record)
    return records


def export_render_specs(
    cases: Iterable[BenchmarkRecord],
    output_path: Path = DEFAULT_RENDER_SPECS_PATH,
    limit: int | None = None,
) -> int:
    """Write canonical UTF-8 JSONL containing only safe render specs."""
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
        raise ValueError("limit must be a positive integer or None")
    lines: list[bytes] = []
    seen: set[str] = set()
    for case in cases:
        if limit is not None and len(lines) >= limit:
            break
        if case.case_id in seen:
            raise ValueError(f"duplicate case_id {case.case_id!r}")
        seen.add(case.case_id)
        spec = build_render_spec(case)
        line = json.dumps(
            spec.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8") + b"\n"
        lines.append(line)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"".join(lines))
    return len(lines)
