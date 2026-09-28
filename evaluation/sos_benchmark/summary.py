"""Validate synthetic SOS JSONL and summarize its design distributions.

This reports properties of generated records. It does not conduct human review or
measure extraction performance.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .models import BenchmarkRecord
from .rubric import visible_signal_bucket

FACT_NAMES = (
    "people_count",
    "injury_reported",
    "urgent_signs",
    "vulnerable_groups",
    "trapped",
    "water_signals",
    "fire_signals",
    "structure_signals",
    "access_observations",
    "requested_assistance",
    "incident_location",
)
FIELD_STATES = ("supported", "uncertain", "unknown", "conflicting")
SPLITS = ("train", "dev", "test")
AXES = (
    "signal_bucket",
    "primary_challenge",
    "count_mode",
    "injury_mode",
    "location_mode",
)
GROUP_IDS = ("template_family_id", "incident_group_id", "paired_variant_group_id")

# Design quotas from docs/Synthetic SOS Benchmark v1.md, section 1.3. These are
# stress-test targets, not estimates of real disaster incidence.
TARGET_PERCENTAGES: dict[str, dict[str, float]] = {
    "split": {"train": 60, "dev": 20, "test": 20},
    "signal_bucket": {"lower": 30, "intermediate": 35, "high": 35},
    "primary_challenge": {
        "ordinary": 35,
        "noisy": 25,
        "missing": 15,
        "vague": 15,
        "conflicting": 10,
    },
    "count_mode": {
        "exact": 55,
        "range": 10,
        "vague": 15,
        "omitted": 15,
        "conflicting": 5,
    },
    "injury_mode": {
        "positive": 30,
        "negative": 20,
        "omitted": 37,
        "hedged": 10,
        "conflicting": 3,
    },
    "location_mode": {
        "incident_pin": 40,
        "text_location": 35,
        "reporter_gps": 10,
        "absent": 10,
        "competing": 5,
    },
    "reported_prevalence": {
        "vulnerable_groups": 25,
        "water_signals": 55,
        "fire_signals": 8,
        "structure_signals": 12,
    },
    "style_flags": {
        "informal": 40,
        "typo": 20,
        "fragment": 25,
        "irrelevant": 25,
        "urgency": 25,
    },
}

AUDIT_NOTE = (
    "Human review values are copied from record metadata; this script only "
    "performs schema validation and distribution counting."
)


class _Counts:
    """Mutable counters for one split or all records."""

    def __init__(self) -> None:
        self.total = 0
        self.axes: dict[str, Counter[str]] = {axis: Counter() for axis in AXES}
        self.field_states: dict[str, Counter[str]] = {field: Counter() for field in FACT_NAMES}
        self.metadata: defaultdict[str, Counter[str]] = defaultdict(Counter)
        self.prevalence: Counter[str] = Counter()
        self.style_flags: Counter[str] = Counter()

    def add(self, record: BenchmarkRecord) -> None:
        data = record.model_dump(mode="json")
        generation = data["generation"]
        gold = data["ground_truth"]["gold_annotation"]
        facts = gold["facts"]
        self.total += 1

        for axis in AXES:
            self.axes[axis][str(generation[axis])] += 1
        for field in FACT_NAMES:
            self.field_states[field][str(facts[field]["state"])] += 1

        for field in TARGET_PERCENTAGES["reported_prevalence"]:
            fact = facts[field]
            if fact["state"] == "supported" and fact["value"]:
                self.prevalence[field] += 1
        for flag in generation["style_flags"]:
            self.style_flags[str(flag)] += 1

        self.metadata["synthetic"][_metadata_value(data["synthetic"])] += 1
        self.metadata["human_review"][_metadata_value(data["qc"]["human_review"])] += 1
        self.metadata["human_audit"][_metadata_value(gold["human_audit"])] += 1
        for name, value in data["metadata"].items():
            self.metadata[f"metadata.{name}"][_metadata_value(value)] += 1
        for name, value in data["versions"].items():
            self.metadata[f"versions.{name}"][_metadata_value(value)] += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "axes": {
                axis: _distribution(self.axes[axis], self.total, TARGET_PERCENTAGES[axis])
                for axis in AXES
            },
            "gold_field_states": {
                field: _distribution(self.field_states[field], self.total, FIELD_STATES)
                for field in FACT_NAMES
            },
            "reported_prevalence": _distribution(
                self.prevalence,
                self.total,
                TARGET_PERCENTAGES["reported_prevalence"],
            ),
            "style_flags": _distribution(
                self.style_flags, self.total, TARGET_PERCENTAGES["style_flags"]
            ),
            "metadata": {
                key: _distribution(counter, self.total)
                for key, counter in sorted(self.metadata.items())
            },
        }


def _metadata_value(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _distribution(
    counts: Counter[str],
    denominator: int,
    categories: Mapping[str, float] | tuple[str, ...] = (),
) -> dict[str, dict[str, int | float]]:
    keys = dict.fromkeys((*categories, *sorted(counts)))
    targets = categories if isinstance(categories, Mapping) else {}
    result: dict[str, dict[str, int | float]] = {}
    for category in keys:
        count = counts[category]
        item: dict[str, int | float] = {
            "count": count,
            "percentage": round(100 * count / denominator, 2) if denominator else 0.0,
        }
        if category in targets:
            item["target_percentage"] = targets[category]
        result[category] = item
    return result


def summarize_file(path: Path) -> dict[str, Any]:
    """Validate every physical JSONL line, then count distributions.

    Invalid records, blank lines and duplicate IDs fail with a line-numbered
    error. The input is the private benchmark record, not a model-facing export.
    """

    overall = _Counts()
    per_split = {split: _Counts() for split in SPLITS}
    case_ids: set[str] = set()
    group_owners: dict[str, dict[str, tuple[str, int]]] = {group_id: {} for group_id in GROUP_IDS}
    with path.open("r", encoding="utf-8", newline="") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                raise ValueError(f"{path}:{line_number}: blank JSONL line")
            try:
                record = BenchmarkRecord.model_validate_json(line)
            except ValidationError as exc:
                raise ValueError(f"{path}:{line_number}: invalid benchmark record: {exc}") from exc
            if record.case_id in case_ids:
                raise ValueError(f"{path}:{line_number}: duplicate case_id {record.case_id!r}")
            case_ids.add(record.case_id)
            visible_bucket = visible_signal_bucket(record.ground_truth.gold_annotation.facts)
            recorded_bucket = record.generation.signal_bucket
            if recorded_bucket != visible_bucket:
                raise ValueError(
                    f"{path}:{line_number}: signal_bucket {recorded_bucket.value!r} "
                    f"does not match visible gold cues ({visible_bucket.value!r})"
                )
            split = str(record.split)
            if split not in per_split:
                raise ValueError(f"{path}:{line_number}: unsupported split {split!r}")
            for group_id in GROUP_IDS:
                value = getattr(record.generation, group_id)
                if value is None:
                    continue
                owner = group_owners[group_id].get(value)
                if owner is not None and owner[0] != split:
                    raise ValueError(
                        f"{path}:{line_number}: {group_id} {value!r} appears in "
                        f"{owner[0]!r} (line {owner[1]}) and {split!r} splits"
                    )
                group_owners[group_id].setdefault(value, (split, line_number))
            overall.add(record)
            per_split[split].add(record)
    if overall.total == 0:
        raise ValueError(f"{path}: empty JSONL file")

    return {
        "total": overall.total,
        "split_distribution": _distribution(
            Counter({split: group.total for split, group in per_split.items()}),
            overall.total,
            TARGET_PERCENTAGES["split"],
        ),
        "overall": overall.as_dict(),
        "per_split": {split: group.as_dict() for split, group in per_split.items()},
        "target_percentages": TARGET_PERCENTAGES,
        "audit_note": AUDIT_NOTE,
    }


def _format_distribution(
    distribution: Mapping[str, Mapping[str, int | float]], *, indent: str = "  "
) -> list[str]:
    lines: list[str] = []
    for category, values in distribution.items():
        target = values.get("target_percentage")
        target_text = f", target {target:g}%" if target is not None else ""
        lines.append(
            f"{indent}{category}: {values['count']} ({values['percentage']:g}%{target_text})"
        )
    return lines


def format_summary(summary: Mapping[str, Any]) -> str:
    """Render a readable, deterministic report with both targets and outcomes."""

    lines = [
        "Synthetic SOS benchmark distribution",
        f"Records: {summary['total']}",
        "Targets are stress-test design percentages, not real incident prevalence.",
        str(summary["audit_note"]),
        "Split distribution:",
        *_format_distribution(summary["split_distribution"]),
    ]
    groups = [("Overall", summary["overall"])] + [
        (f"Split {split}", summary["per_split"][split]) for split in SPLITS
    ]
    for title, group in groups:
        lines.extend(("", f"{title} (n={group['total']}):"))
        for axis in AXES:
            lines.append(f"  {axis}:")
            lines.extend(_format_distribution(group["axes"][axis], indent="    "))
        lines.append("  gold_field_states:")
        for field in FACT_NAMES:
            values = group["gold_field_states"][field]
            rendered = ", ".join(
                f"{state} {values[state]['count']} ({values[state]['percentage']:g}%)"
                for state in FIELD_STATES
            )
            lines.append(f"    {field}: {rendered}")
        lines.append("  reported_prevalence:")
        lines.extend(_format_distribution(group["reported_prevalence"], indent="    "))
        lines.append("  style_flags (overlapping):")
        lines.extend(_format_distribution(group["style_flags"], indent="    "))
        lines.append("  metadata:")
        for key, values in group["metadata"].items():
            rendered = ", ".join(
                f"{value}={cell['count']} ({cell['percentage']:g}%)"
                for value, cell in values.items()
            )
            lines.append(f"    {key}: {rendered}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="Private synthetic benchmark JSONL file")
    parser.add_argument("--json", action="store_true", help="Write machine-readable JSON summary")
    args = parser.parse_args(argv)
    try:
        summary = summarize_file(args.path)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print(format_summary(summary), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
