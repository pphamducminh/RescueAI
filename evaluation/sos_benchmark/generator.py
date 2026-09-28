"""Deterministic, offline Vietnamese SOS benchmark generator.

The order of operations is deliberate: sample the simulated incident, freeze
the reporter's semantic claims, render annotated text, then attach evidence
offsets to those already planned claims. Gold is never inferred from text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import random
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

from pydantic import __version__ as PYDANTIC_VERSION

from backend.domain.sos_types import (
    AccessMode,
    AccessStatus,
    Assertion,
    AssistanceTag,
    EvidenceSource,
    FieldState,
    FireTag,
    GeoPoint,
    LocationPrecision,
    LocationRole,
    LocationSource,
    StructureTag,
    UnknownReason,
    UrgentSign,
    VulnerableGroupKind,
    WaterTag,
)
from evaluation.sos_benchmark.models import (
    BenchmarkRecord,
    CommunicationPlan,
    CountMode,
    FactPath,
    GoldFacts,
    InjuryMode,
    LatentScenario,
    LocationMode,
    PlannedClaim,
    PrimaryChallenge,
    SignalBucket,
    Split,
    StyleFlag,
)
from evaluation.sos_benchmark.rubric import visible_signal_bucket
from evaluation.sos_benchmark.templates import (
    CONFLICTING_LOCATIONS,
    FAMILIES,
    PHRASES,
    TemplateFamily,
)

AxisValue = TypeVar("AxisValue", bound=str)
SPLIT_WEIGHTS: tuple[tuple[Split, int], ...] = (
    (Split.TRAIN, 60),
    (Split.DEV, 20),
    (Split.TEST, 20),
)
AXIS_WEIGHTS: dict[str, tuple[tuple[str, int], ...]] = {
    "signal_bucket": (("lower", 30), ("intermediate", 35), ("high", 35)),
    "primary_challenge": (
        ("ordinary", 35),
        ("noisy", 25),
        ("missing", 15),
        ("vague", 15),
        ("conflicting", 10),
    ),
    "count_mode": (
        ("exact", 55),
        ("range", 10),
        ("vague", 15),
        ("omitted", 15),
        ("conflicting", 5),
    ),
    "injury_mode": (
        ("positive", 30),
        ("negative", 20),
        ("omitted", 37),
        ("hedged", 10),
        ("conflicting", 3),
    ),
    "location_mode": (
        ("incident_pin", 40),
        ("text_location", 35),
        ("reporter_gps", 10),
        ("absent", 10),
        ("competing", 5),
    ),
}
STYLE_WEIGHTS: tuple[tuple[StyleFlag, int], ...] = (
    (StyleFlag.INFORMAL, 40),
    (StyleFlag.TYPO, 20),
    (StyleFlag.FRAGMENT, 25),
    (StyleFlag.IRRELEVANT, 25),
    (StyleFlag.URGENCY, 25),
)
BASE_TIME = datetime(2026, 9, 28, 4, 0, tzinfo=UTC)
INFORMAL_PREFIX: dict[Split, str] = {
    Split.TRAIN: "Mọi ng ơi, ",
    Split.DEV: "Mình nói nè, ",
    Split.TEST: "Cho mình báo nha, ",
}
# Vetted typo variants apply only to unannotated frame text.
SAFE_TYPOS: tuple[tuple[str, str], ...] = (
    ("Mọi", "Mọii"),
    ("Tôi", "Toi"),
    ("Mình", "Minh"),
    ("Em", "Eem"),
    ("Tin", "Tn"),
    ("Tình", "Tìn"),
    ("Đây", "Dây"),
    ("Có", "Co"),
    ("Báo", "Bao"),
    ("Hiện", "Hien"),
    ("Cho", "Choo"),
    ("Lời", "Lơi"),
    ("Từ", "Tu"),
    ("Ở", "Ơ"),
)


@dataclass(frozen=True)
class CaseTarget:
    signal_bucket: SignalBucket
    primary_challenge: PrimaryChallenge
    count_mode: CountMode
    injury_mode: InjuryMode
    location_mode: LocationMode
    style_flags: tuple[StyleFlag, ...]
    zero_count: bool = False
    hazards_planned: bool = False
    water_cue: bool = False
    fire_cue: bool = False
    structure_cue: bool = False


@dataclass(frozen=True)
class RenderedMessage:
    text: str
    # Claim index -> exact final-text Unicode code-point offsets and quote.
    trace: Mapping[int, tuple[int, int, str]]


def stable_seed(master_seed: int, split: Split, slot: int, attempt: int, stage: str) -> int:
    """Derive independent stage seeds without process-randomized Python hash()."""
    payload = json.dumps(
        [master_seed, split.value, slot, attempt, stage],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest(), "big")


def _rounded_quota(total: int, weights: Sequence[tuple[AxisValue, int]]) -> dict[AxisValue, int]:
    if sum(weight for _, weight in weights) != 100:
        raise ValueError("quota weights must sum to 100")
    result = {key: total * weight // 100 for key, weight in weights}
    remainder = total - sum(result.values())
    ranked = sorted(
        enumerate(weights),
        key=lambda pair: (-(total * pair[1][1] % 100), pair[0]),
    )
    for _, (key, _) in ranked[:remainder]:
        result[key] += 1
    return result


def _split_quota(
    split_sizes: Mapping[Split, int], weights: Sequence[tuple[AxisValue, int]]
) -> dict[Split, dict[AxisValue, int]]:
    """Round globally exact quotas, then balance their allocations by split."""
    global_quota = _rounded_quota(sum(split_sizes.values()), weights)
    result = {
        split: {key: size * weight // 100 for key, weight in weights}
        for split, size in split_sizes.items()
    }
    row_left = {split: split_sizes[split] - sum(row.values()) for split, row in result.items()}
    column_left = {
        key: global_quota[key] - sum(result[split][key] for split in split_sizes)
        for key, _ in weights
    }
    ordered_splits = tuple(split_sizes)
    ordered_keys = tuple(key for key, _ in weights)
    while any(row_left.values()):
        eligible = [
            (split, key)
            for split in ordered_splits
            for key in ordered_keys
            if row_left[split] and column_left[key]
        ]
        if not eligible:
            raise ValueError("could not balance axis quotas across splits")
        split, key = max(
            eligible,
            key=lambda cell: (
                split_sizes[cell[0]] * dict(weights)[cell[1]] % 100,
                -ordered_splits.index(cell[0]),
                -ordered_keys.index(cell[1]),
            ),
        )
        result[split][key] += 1
        row_left[split] -= 1
        column_left[key] -= 1
    return result


def _assign_axis(
    split: Split,
    seed: int,
    axis: str,
    quotas: Mapping[str, int],
) -> list[str]:
    values = [name for name, count in quotas.items() for _ in range(count)]
    random.Random(stable_seed(seed, split, 0, 0, f"axis:{axis}")).shuffle(values)
    return values


def _move_category_into_primary(
    *,
    assignments: dict[str, dict[Split, list[str]]],
    split: Split,
    axis: str,
    category: str,
    primary: str,
    seed: int,
) -> None:
    """Keep a categorical challenge inside its corresponding primary stratum."""
    categories = assignments[axis][split]
    primaries = assignments["primary_challenge"][split]
    outside = [
        index
        for index, value in enumerate(categories)
        if value == category and primaries[index] != primary
    ]
    inside = [
        index
        for index, value in enumerate(categories)
        if value != category and primaries[index] == primary
    ]
    rng = random.Random(stable_seed(seed, split, 0, 0, f"align:{axis}:{category}"))
    rng.shuffle(outside)
    rng.shuffle(inside)
    # Tiny smoke samples may round this category above the primary quota.
    # Move as many as fit while preserving every marginal axis count.
    for from_index, to_index in zip(outside, inside, strict=False):
        categories[from_index], categories[to_index] = categories[to_index], categories[from_index]


def _choose_slots(slots: Sequence[int], n: int, seed: int, split: Split, stage: str) -> set[int]:
    pool = list(slots)
    random.Random(stable_seed(seed, split, 0, 0, stage)).shuffle(pool)
    if n > len(pool):
        raise ValueError(f"insufficient slots for {stage}: need {n}, have {len(pool)}")
    return set(pool[:n])


def _plan_hazard_cues(
    targets: dict[Split, list[CaseTarget]], sizes: Mapping[Split, int], seed: int
) -> None:
    """Balance visible water/fire/structure coverage inside each split."""
    quotas = {
        name: _split_quota(sizes, (("yes", percentage), ("no", 100 - percentage)))
        for name, percentage in (("water", 55), ("fire", 8), ("structure", 12))
    }
    for split, group in targets.items():
        high = [i for i, target in enumerate(group) if target.signal_bucket is SignalBucket.HIGH]
        intermediate = [
            i for i, target in enumerate(group) if target.signal_bucket is SignalBucket.INTERMEDIATE
        ]
        fire_n = quotas["fire"][split]["yes"]
        structure_n = quotas["structure"][split]["yes"]
        water_n = quotas["water"][split]["yes"]
        high_fire_n = min(len(high), (fire_n * 3 + 3) // 4)
        fire_high = _choose_slots(high, high_fire_n, seed, split, "hazard:fire:high")
        fire_mid = _choose_slots(
            intermediate, fire_n - high_fire_n, seed, split, "hazard:fire:intermediate"
        )
        high_available = [i for i in high if i not in fire_high]
        mid_available = [i for i in intermediate if i not in fire_mid]
        high_structure_n = min(len(high_available), (structure_n * 3 + 3) // 4)
        structure_high = _choose_slots(
            high_available, high_structure_n, seed, split, "hazard:structure:high"
        )
        structure_mid = _choose_slots(
            mid_available,
            structure_n - high_structure_n,
            seed,
            split,
            "hazard:structure:intermediate",
        )
        mid_available = [i for i in intermediate if i not in fire_mid and i not in structure_mid]
        water_mid = _choose_slots(
            mid_available, water_n - len(high), seed, split, "hazard:water:intermediate"
        )
        for index, target in enumerate(group):
            group[index] = replace(
                target,
                hazards_planned=True,
                water_cue=index in high or index in water_mid,
                fire_cue=index in fire_high or index in fire_mid,
                structure_cue=index in structure_high or index in structure_mid,
            )


def _case_targets(count: int, seed: int) -> dict[Split, list[CaseTarget]]:
    sizes_by_name = _rounded_quota(
        count, tuple((split.value, weight) for split, weight in SPLIT_WEIGHTS)
    )
    sizes = {split: sizes_by_name[split.value] for split, _ in SPLIT_WEIGHTS}
    assignments: dict[str, dict[Split, list[str]]] = {}
    for axis, weights in AXIS_WEIGHTS.items():
        quota = _split_quota(sizes, weights)
        assignments[axis] = {
            split: _assign_axis(split, seed, axis, quota[split]) for split in sizes
        }
    for split in sizes:
        for axis, category, primary in (
            ("count_mode", "conflicting", "conflicting"),
            ("injury_mode", "conflicting", "conflicting"),
            ("location_mode", "competing", "conflicting"),
            ("count_mode", "vague", "vague"),
            ("injury_mode", "hedged", "vague"),
        ):
            _move_category_into_primary(
                assignments=assignments,
                split=split,
                axis=axis,
                category=category,
                primary=primary,
                seed=seed,
            )
    styles: dict[StyleFlag, dict[Split, list[str]]] = {}
    for flag, yes_weight in STYLE_WEIGHTS:
        quota = _split_quota(sizes, (("yes", yes_weight), ("no", 100 - yes_weight)))
        styles[flag] = {
            split: _assign_axis(split, seed, f"style:{flag.value}", quota[split]) for split in sizes
        }
    targets: dict[Split, list[CaseTarget]] = {}
    for split, size in sizes.items():
        targets[split] = [
            CaseTarget(
                signal_bucket=SignalBucket(assignments["signal_bucket"][split][slot]),
                primary_challenge=PrimaryChallenge(assignments["primary_challenge"][split][slot]),
                count_mode=CountMode(assignments["count_mode"][split][slot]),
                injury_mode=InjuryMode(assignments["injury_mode"][split][slot]),
                location_mode=LocationMode(assignments["location_mode"][split][slot]),
                style_flags=tuple(
                    flag for flag, _ in STYLE_WEIGHTS if styles[flag][split][slot] == "yes"
                ),
            )
            for slot in range(size)
        ]
    if count >= 100:
        _plan_hazard_cues(targets, sizes, seed)
    # Preserve explicit-zero count coverage in a sensible lower-load case.
    # This is an exact count, distinct from an omitted/unknown count.
    for split, _ in SPLIT_WEIGHTS:
        eligible = next(
            (
                index
                for index, target in enumerate(targets[split])
                if target.count_mode is CountMode.EXACT
                and target.injury_mode is InjuryMode.NEGATIVE
                and target.signal_bucket is SignalBucket.LOWER
                and target.primary_challenge in {PrimaryChallenge.ORDINARY, PrimaryChallenge.NOISY}
            ),
            None,
        )
        if eligible is not None:
            targets[split][eligible] = replace(targets[split][eligible], zero_count=True)
            break
    return targets


def sample_latent_scenario(target: CaseTarget, split: Split, rng: random.Random) -> LatentScenario:
    """Sample the concealed incident before deciding what the reporter communicates."""
    people_count = (
        0 if target.zero_count else rng.randint(1, 5) if rng.random() < 0.9 else rng.randint(6, 20)
    )
    injury_present = (
        True
        if target.injury_mode is InjuryMode.POSITIVE
        else False
        if target.injury_mode is InjuryMode.NEGATIVE
        else rng.random() < 0.5
    )
    urgent_sign: UrgentSign | None = None
    trapped = water = fire = structure = access = False
    if target.hazards_planned:
        water = target.water_cue
        fire = target.fire_cue
        structure = target.structure_cue
        if target.signal_bucket is SignalBucket.HIGH and not (fire or structure):
            if rng.random() < 0.55:
                urgent_sign = rng.choice(
                    (UrgentSign.UNCONSCIOUS_REPORTED, UrgentSign.BREATHING_DIFFICULTY_REPORTED)
                )
            else:
                trapped = True
        elif target.signal_bucket is SignalBucket.INTERMEDIATE and not (water or fire or structure):
            if target.primary_challenge is PrimaryChallenge.CONFLICTING or rng.random() < 0.7:
                trapped = True
            else:
                access = True
        elif target.signal_bucket is SignalBucket.LOWER:
            water = rng.random() < 0.1  # Concealed and omitted from the report.
    elif target.signal_bucket is SignalBucket.HIGH:
        pattern = rng.random()
        if pattern < 0.4:
            urgent_sign = rng.choice(
                (UrgentSign.UNCONSCIOUS_REPORTED, UrgentSign.BREATHING_DIFFICULTY_REPORTED)
            )
            water = rng.random() < 0.85
            structure = rng.random() < 0.2
        elif pattern < 0.75:
            trapped = water = True
        else:
            water = True
            fire = rng.random() < 0.45
            structure = not fire
    elif target.signal_bucket is SignalBucket.INTERMEDIATE:
        cue = rng.choices(
            ("water", "fire", "structure", "trapped", "access"),
            weights=(
                68,
                10,
                10,
                7,
                5 if target.primary_challenge is not PrimaryChallenge.CONFLICTING else 0,
            ),
            k=1,
        )[0]
        water = cue == "water"
        fire = cue == "fire"
        structure = cue == "structure"
        trapped = cue == "trapped"
        access = cue == "access"
    else:
        # A concealed cue is occasionally present even though it will be omitted.
        water = rng.random() < 0.1
    if target.zero_count:
        vulnerable_group = None
    elif target.primary_challenge is PrimaryChallenge.MISSING:
        vulnerable_group = rng.choice((VulnerableGroupKind.CHILD, VulnerableGroupKind.OLDER_ADULT))
    elif rng.random() < 0.3:
        vulnerable_group = rng.choice(tuple(VulnerableGroupKind)[:5])
    else:
        vulnerable_group = None
    incident_point = GeoPoint(
        lat=round(15.5 + rng.random(), 6),
        lon=round(106.0 + rng.random(), 6),
    )
    incident_landmark = (
        CONFLICTING_LOCATIONS[split.value][0]
        if target.location_mode is LocationMode.COMPETING
        else rng.choice(PHRASES[split.value]["location"])
    )
    return LatentScenario(
        people_count=people_count,
        injury_present=injury_present,
        urgent_sign=urgent_sign,
        trapped=trapped,
        water_active=water,
        fire_active=fire,
        structure_active=structure,
        access_blocked=access,
        vulnerable_group=vulnerable_group,
        location_kind=LocationPrecision.POINT,
        location_role="incident",
        incident_point=incident_point,
        incident_landmark=incident_landmark,
        incident_time=BASE_TIME - timedelta(minutes=rng.randint(1, 180)),
    )


def _latent_bucket(latent: LatentScenario) -> SignalBucket:
    hazards = sum((latent.water_active, latent.fire_active, latent.structure_active))
    if latent.urgent_sign is not None or (latent.trapped and hazards) or hazards >= 2:
        return SignalBucket.HIGH
    if hazards or latent.trapped or latent.access_blocked:
        return SignalBucket.INTERMEDIATE
    return SignalBucket.LOWER


def plan_communication(
    latent: LatentScenario,
    target: CaseTarget,
    split: Split,
    rng: random.Random,
) -> tuple[CommunicationPlan, dict[str, Any] | None]:
    """Freeze every semantic claim and omission before any text is rendered."""
    claims: list[PlannedClaim] = []
    hedged: set[FactPath] = set()
    conflicting: set[FactPath] = set()

    def add(
        field: FactPath,
        value: Any,
        *,
        assertion: Assertion = Assertion.STATED,
        witness: str = "reporter",
    ) -> None:
        claims.append(
            PlannedClaim(
                field_path=field.value,
                value=value,
                assertion=assertion,
                witness_id=witness,
                scope_id="incident",
                time_ref="current",
            )
        )
        if assertion is Assertion.HEDGED:
            hedged.add(field)

    if target.count_mode is CountMode.EXACT:
        add(FactPath.PEOPLE_COUNT, {"min": latent.people_count, "max": latent.people_count})
    elif target.count_mode is CountMode.RANGE:
        low = max(1, latent.people_count - 1)
        high: int | None = latent.people_count + 1 if rng.random() < 0.5 else None
        add(FactPath.PEOPLE_COUNT, {"min": low, "max": high})
    elif target.count_mode is CountMode.VAGUE:
        add(FactPath.PEOPLE_COUNT, None, assertion=Assertion.HEDGED)
    elif target.count_mode is CountMode.CONFLICTING:
        add(
            FactPath.PEOPLE_COUNT,
            {"min": latent.people_count, "max": latent.people_count},
            witness="witness_a",
        )
        other_count = latent.people_count + rng.randint(2, 4)
        add(FactPath.PEOPLE_COUNT, {"min": other_count, "max": other_count}, witness="witness_b")
        conflicting.add(FactPath.PEOPLE_COUNT)

    if target.injury_mode is InjuryMode.POSITIVE:
        add(FactPath.INJURY_REPORTED, True)
    elif target.injury_mode is InjuryMode.NEGATIVE:
        add(FactPath.INJURY_REPORTED, False)
    elif target.injury_mode is InjuryMode.HEDGED:
        add(FactPath.INJURY_REPORTED, True, assertion=Assertion.HEDGED)
    elif target.injury_mode is InjuryMode.CONFLICTING:
        add(FactPath.INJURY_REPORTED, True, witness="witness_a")
        add(FactPath.INJURY_REPORTED, False, witness="witness_b")
        conflicting.add(FactPath.INJURY_REPORTED)

    if latent.urgent_sign is not None:
        add(FactPath.URGENT_SIGNS, [latent.urgent_sign.value])
    if latent.trapped:
        add(FactPath.TRAPPED, True)
    # On lower-load cases, a latent hazard is deliberately not reported.
    if target.signal_bucket is not SignalBucket.LOWER:
        if latent.water_active:
            add(FactPath.WATER_SIGNALS, [WaterTag.WATER_RISING_REPORTED.value])
        if latent.fire_active:
            add(FactPath.FIRE_SIGNALS, [FireTag.SMOKE_OBSERVED.value])
        if latent.structure_active:
            add(FactPath.STRUCTURE_SIGNALS, [StructureTag.CRACKS_REPORTED.value])
        if latent.access_blocked:
            add(
                FactPath.ACCESS_OBSERVATIONS,
                [{"mode": AccessMode.VEHICLE.value, "status": AccessStatus.BLOCKED_REPORTED.value}],
            )

    if (
        latent.vulnerable_group is not None
        and target.primary_challenge is not PrimaryChallenge.MISSING
    ):
        add(
            FactPath.VULNERABLE_GROUPS,
            [{"group": latent.vulnerable_group.value, "count": None}],
        )
    elif latent.vulnerable_group is None and rng.random() < 0.04:
        add(FactPath.VULNERABLE_GROUPS, [])
    elif latent.vulnerable_group is None and rng.random() < 0.04:
        add(
            FactPath.VULNERABLE_GROUPS,
            [{"group": VulnerableGroupKind.CHILD.value, "count": None}],
            assertion=Assertion.HEDGED,
        )

    if target.primary_challenge is PrimaryChallenge.VAGUE:
        add(
            FactPath.REQUESTED_ASSISTANCE,
            [AssistanceTag.BOAT.value if latent.water_active else AssistanceTag.EVACUATION.value],
            assertion=Assertion.HEDGED,
        )
    elif latent.people_count > 0 and rng.random() < 0.5:
        assistance = (
            AssistanceTag.MEDICAL_TEAM
            if target.injury_mode is InjuryMode.POSITIVE
            else AssistanceTag.BOAT
            if latent.water_active
            else AssistanceTag.EVACUATION
        )
        add(FactPath.REQUESTED_ASSISTANCE, [assistance.value])

    submitted_location: dict[str, Any] | None = None
    if target.location_mode in {LocationMode.INCIDENT_PIN, LocationMode.REPORTER_GPS}:
        point = (
            latent.incident_point.model_dump(mode="json")
            if target.location_mode is LocationMode.INCIDENT_PIN
            else {
                "lat": round(latent.incident_point.lat + 0.003, 6),
                "lon": round(latent.incident_point.lon + 0.003, 6),
            }
        )
        role = (
            LocationRole.INCIDENT
            if target.location_mode is LocationMode.INCIDENT_PIN
            else LocationRole.REPORTER
        )
        submitted_location = {
            "point": point,
            "role": role.value,
            "source": LocationSource.MAP_PIN.value
            if role is LocationRole.INCIDENT
            else LocationSource.DEVICE_GPS.value,
            "accuracy_m": None,
            "captured_at": None,
        }
        if role is LocationRole.INCIDENT:
            add(
                FactPath.INCIDENT_LOCATION,
                {"description": None, "point": point, "precision": LocationPrecision.POINT.value},
            )
    elif target.location_mode is LocationMode.TEXT_LOCATION:
        description = latent.incident_landmark
        add(
            FactPath.INCIDENT_LOCATION,
            {
                "description": description,
                "point": None,
                "precision": LocationPrecision.LANDMARK.value,
            },
        )
    elif target.location_mode is LocationMode.COMPETING:
        pair = CONFLICTING_LOCATIONS[split.value]
        for description, witness in zip(pair, ("witness_a", "witness_b"), strict=True):
            add(
                FactPath.INCIDENT_LOCATION,
                {
                    "description": description,
                    "point": None,
                    "precision": LocationPrecision.LANDMARK.value,
                },
                witness=witness,
            )
        conflicting.add(FactPath.INCIDENT_LOCATION)

    if target.primary_challenge is PrimaryChallenge.CONFLICTING and not conflicting:
        # Same incident, moment and vehicle mode; the two witnesses disagree.
        claims = [
            claim for claim in claims if claim.field_path != FactPath.ACCESS_OBSERVATIONS.value
        ]
        add(
            FactPath.ACCESS_OBSERVATIONS,
            [{"mode": AccessMode.VEHICLE.value, "status": AccessStatus.BLOCKED_REPORTED.value}],
            witness="witness_a",
        )
        add(
            FactPath.ACCESS_OBSERVATIONS,
            [{"mode": AccessMode.VEHICLE.value, "status": AccessStatus.PASSABLE_REPORTED.value}],
            witness="witness_b",
        )
        conflicting.add(FactPath.ACCESS_OBSERVATIONS)

    mentioned = {FactPath(claim.field_path) for claim in claims}
    all_fields = tuple(FactPath)
    actual_style = list(target.style_flags)
    if target.primary_challenge is PrimaryChallenge.NOISY and not actual_style:
        actual_style.append(StyleFlag.INFORMAL)
    noise_edits = tuple(flag.value for flag in actual_style)
    plan = CommunicationPlan(
        mentioned_fields=tuple(field for field in all_fields if field in mentioned),
        omitted_fields=tuple(field for field in all_fields if field not in mentioned),
        hedged_fields=tuple(field for field in all_fields if field in hedged),
        conflict_fields=tuple(field for field in all_fields if field in conflicting),
        noise_edits=noise_edits,
        planned_claims=tuple(claims),
    )
    return plan, submitted_location


def _pick(split: Split, key: str, rng: random.Random) -> str:
    return rng.choice(PHRASES[split.value][key])


def _surface_claim(
    claim: PlannedClaim, target: CaseTarget, split: Split, rng: random.Random
) -> str:
    field = FactPath(claim.field_path)
    value: Any = claim.value
    bank = PHRASES[split.value]
    if field is FactPath.PEOPLE_COUNT:
        if value is None:
            phrase = _pick(split, "count_vague", rng)
        elif value["min"] == 0 and value["max"] == 0:
            phrase = _pick(split, "count_zero", rng)
        elif value["max"] is None:
            phrase = bank["count_range"][1].format(low=value["min"])
        elif value["min"] != value["max"]:
            phrase = bank["count_range"][0].format(low=value["min"], high=value["max"])
        elif claim.witness_id != "reporter":
            phrase = bank["count_exact"][0].format(n=value["min"])
        else:
            phrase = _pick(split, "count_exact", rng).format(n=value["min"])
    elif field is FactPath.INJURY_REPORTED:
        phrase = _pick(
            split,
            "injury_hedged"
            if claim.assertion is Assertion.HEDGED
            else "injury_yes"
            if value
            else "injury_no",
            rng,
        )
    elif field is FactPath.URGENT_SIGNS:
        phrase = bank["urgent"][0 if value[0] == UrgentSign.UNCONSCIOUS_REPORTED.value else 1]
    elif field is FactPath.TRAPPED:
        phrase = _pick(split, "trapped", rng)
    elif field is FactPath.WATER_SIGNALS:
        phrase = _pick(split, "water", rng)
    elif field is FactPath.FIRE_SIGNALS:
        phrase = _pick(split, "fire", rng)
    elif field is FactPath.STRUCTURE_SIGNALS:
        phrase = _pick(split, "structure", rng)
    elif field is FactPath.ACCESS_OBSERVATIONS:
        key = (
            "access_blocked"
            if value[0]["status"] == AccessStatus.BLOCKED_REPORTED.value
            else "access_passable"
        )
        phrase = _pick(split, key, rng)
    elif field is FactPath.REQUESTED_ASSISTANCE:
        key = {
            AssistanceTag.BOAT.value: "boat",
            AssistanceTag.MEDICAL_TEAM.value: "medical",
            AssistanceTag.EVACUATION.value: "evacuation",
        }[value[0]]
        phrase = _pick(split, key, rng)
        if claim.assertion is Assertion.HEDGED:
            phrase = f"có lẽ {phrase}"
    elif field is FactPath.VULNERABLE_GROUPS:
        if not value:
            phrase = _pick(split, "vulnerable_none", rng)
        elif claim.assertion is Assertion.HEDGED:
            phrase = _pick(split, "vulnerable_hedged", rng)
        else:
            group_key = value[0]["group"]
            if group_key == VulnerableGroupKind.LIMITED_MOBILITY_REPORTED.value:
                group_key = "limited_mobility"
            phrase = _pick(split, group_key, rng)
    elif field is FactPath.INCIDENT_LOCATION:
        phrase = f"nơi xảy ra sự việc {value['description']}"
    else:
        raise ValueError(f"no surface form for {field}")
    if claim.witness_id == "witness_a":
        return f"người thứ nhất báo {phrase}"
    if claim.witness_id == "witness_b":
        return f"người thứ hai báo {phrase}"
    return phrase


def render_annotated_vietnamese(
    plan: CommunicationPlan,
    family: TemplateFamily,
    target: CaseTarget,
    rng: random.Random,
) -> RenderedMessage:
    """Concatenate annotated segments; record offsets as characters are appended."""
    flags = set(
        StyleFlag(value) for value in plan.noise_edits if value in StyleFlag._value2member_map_
    )
    opener = family.opener
    closer = family.closer
    if StyleFlag.INFORMAL in flags:
        if opener.startswith("Mọi người ơi"):
            opener = opener.replace("Mọi người ơi", "Mọi ng ơi", 1)
        else:
            opener = f"{INFORMAL_PREFIX[Split(family.split)]}{opener[0].lower()}{opener[1:]}"
    if StyleFlag.TYPO in flags:
        for original, variant in SAFE_TYPOS:
            if original in opener:
                opener = opener.replace(original, variant, 1)
                break
        else:
            raise ValueError(f"no vetted typo for template family {family.id}")
    if StyleFlag.FRAGMENT in flags:
        closer = closer.rstrip(".?!")
    pieces: list[str] = []
    trace: dict[int, tuple[int, int, str]] = {}
    cursor = 0

    def append(
        value: str, claim_index: int | None = None, *, separator_override: str | None = None
    ) -> None:
        nonlocal cursor
        if pieces:
            separator = separator_override or ("\n" if StyleFlag.FRAGMENT in flags else ", ")
            pieces.append(separator)
            cursor += len(separator)
        start = cursor
        pieces.append(value)
        cursor += len(value)
        if claim_index is not None:
            trace[claim_index] = (start, cursor, value)

    append(opener)
    order = [
        index
        for index, claim in enumerate(plan.planned_claims)
        if not (
            claim.field_path == FactPath.INCIDENT_LOCATION.value
            and target.location_mode is LocationMode.INCIDENT_PIN
        )
    ]
    rng.shuffle(order)
    for index in order:
        append(_surface_claim(plan.planned_claims[index], target, Split(family.split), rng), index)
    if target.location_mode is LocationMode.INCIDENT_PIN:
        append("đã ghim vị trí được báo")
    elif target.location_mode is LocationMode.REPORTER_GPS:
        append("điểm GPS gửi kèm là chỗ người báo tin đang đứng")
    if StyleFlag.IRRELEVANT in flags:
        append(_pick(Split(family.split), "irrelevant", rng))
    if StyleFlag.URGENCY in flags:
        append(_pick(Split(family.split), "urgency", rng))
    append(closer, separator_override=". ")
    text = "".join(pieces) + "."
    return RenderedMessage(text=text, trace=trace)


def compile_gold(
    plan: CommunicationPlan,
    rendered: RenderedMessage,
    case_id: str,
    location_mode: LocationMode,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Attach trace evidence to frozen plan claims without reading text semantics."""
    evidence: list[dict[str, Any]] = []
    grouped: dict[FactPath, list[dict[str, Any]]] = defaultdict(list)
    for index, claim in enumerate(plan.planned_claims):
        evidence_id = f"e_{index + 1:02d}"
        direct_gps = (
            claim.field_path == FactPath.INCIDENT_LOCATION.value
            and location_mode is LocationMode.INCIDENT_PIN
        )
        if direct_gps:
            text_span = None
            source = EvidenceSource.SUBMITTED_GPS
        else:
            start, end, quote = rendered.trace[index]
            text_span = {"start": start, "end": end, "quote": quote}
            source = EvidenceSource.SOS_TEXT
        evidence.append(
            {
                "id": evidence_id,
                "source_type": source.value,
                "source_ref": case_id,
                "text_span": text_span,
                "image_box": None,
                "captured_at": None,
            }
        )
        grouped[FactPath(claim.field_path)].append(
            {
                "value": claim.value,
                "assertion": claim.assertion.value,
                "evidence_ids": [evidence_id],
                "witness_id": claim.witness_id,
                "scope_id": claim.scope_id,
                "time_ref": claim.time_ref,
            }
        )
    facts: dict[str, Any] = {}
    for field in FactPath:
        field_claims = grouped[field]
        if not field_claims:
            facts[field.value] = {
                "state": FieldState.UNKNOWN.value,
                "value": None,
                "unknown_reason": UnknownReason.NOT_MENTIONED.value,
                "claims": [],
            }
        else:
            state = (
                FieldState.CONFLICTING
                if field in plan.conflict_fields
                else FieldState.UNCERTAIN
                if field in plan.hedged_fields
                else FieldState.SUPPORTED
            )
            facts[field.value] = {
                "state": state.value,
                "value": field_claims[0]["value"] if state is FieldState.SUPPORTED else None,
                "unknown_reason": None,
                "claims": field_claims,
            }
    return facts, evidence


def _phrase_bank_hash() -> str:
    payload = {
        "families": {
            split: [asdict(family) for family in families] for split, families in FAMILIES.items()
        },
        "phrases": PHRASES,
        "conflicting_locations": CONFLICTING_LOCATIONS,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _source_hashes() -> dict[str, str]:
    directory = Path(__file__).resolve().parent
    return {
        f"{name}_source_sha256": hashlib.sha256((directory / filename).read_bytes()).hexdigest()
        for name, filename in (
            ("schema", "models.py"),
            ("generator", "generator.py"),
            ("rubric", "rubric.py"),
        )
    }


def _build_record(
    *,
    case_id: str,
    global_index: int,
    split: Split,
    slot: int,
    seed: int,
    target: CaseTarget,
    phrase_hash: str,
    source_hashes: Mapping[str, str],
) -> BenchmarkRecord:
    attempt = 0
    latent = sample_latent_scenario(
        target, split, random.Random(stable_seed(seed, split, slot, attempt, "latent"))
    )
    plan, submitted_location = plan_communication(
        latent, target, split, random.Random(stable_seed(seed, split, slot, attempt, "plan"))
    )
    family_rng = random.Random(stable_seed(seed, split, slot, attempt, "family"))
    family = family_rng.choice(FAMILIES[split.value])
    rendered = render_annotated_vietnamese(
        plan, family, target, random.Random(stable_seed(seed, split, slot, attempt, "render"))
    )
    facts, evidence = compile_gold(plan, rendered, case_id, target.location_mode)
    visible = visible_signal_bucket(GoldFacts.model_validate(facts))
    if visible is not target.signal_bucket:
        raise ValueError(
            f"case {case_id} violates frozen visible-bucket quota: "
            f"{visible} != {target.signal_bucket}"
        )
    payload = {
        "case_id": case_id,
        "synthetic": True,
        "split": split.value,
        "metadata": {
            "origin": "synthetic_template_rule",
            "language": "vi",
            "generator": "offline_templates_v1",
            "master_seed": seed,
            "sample": True,  # Human review is pending, even at a larger N.
        },
        "versions": {
            "schema": "1.0",
            "generator": "1.0.0",
            "rubric": "1.0",
            "phrase_bank": "pb-v1",
            "phrase_bank_sha256": phrase_hash,
            **source_hashes,
            "python_version": platform.python_version(),
            "pydantic_version": PYDANTIC_VERSION,
            "prng": "python_random_mt19937",
        },
        "generation": {
            "case_seed": stable_seed(seed, split, slot, attempt, "case"),
            "template_family_id": family.id,
            "incident_group_id": f"incident_{split.value}_{slot:06d}",
            "paired_variant_group_id": None,
            "primary_challenge": target.primary_challenge.value,
            "count_mode": target.count_mode.value,
            "injury_mode": target.injury_mode.value,
            "location_mode": target.location_mode.value,
            "signal_bucket": visible.value,
            "latent_signal_bucket": _latent_bucket(latent).value,
            "style_flags": list(plan.noise_edits),
            "attempt": attempt,
        },
        "ground_truth": {
            "latent_scenario": latent.model_dump(mode="json"),
            "communication_plan": plan.model_dump(mode="json"),
            "gold_annotation": {
                "annotation_origin": "render_trace",
                "facts": facts,
                "evidence": evidence,
                "human_audit": "pending",
            },
        },
        "generated_input": {
            "schema_version": "1.0",
            "sos_id": case_id,
            "input_revision": 1,
            "raw_text": rendered.text,
            "image_ref": None,
            "submitted_location": submitted_location,
            "received_at": (BASE_TIME + timedelta(minutes=global_index)).isoformat(),
            "reported_event_at": None,
        },
        "qc": {"schema_valid": True, "trace_valid": True, "human_review": "pending"},
    }
    return BenchmarkRecord.model_validate(payload)


def generate_dataset(count: int, seed: int) -> list[BenchmarkRecord]:
    """Generate a reproducible labeled development sample with disjoint splits.

    ``count`` may be any positive integer so small smoke samples work. The
    methodology's target release size is 1,500; generated data remains a
    synthetic, unaudited sample until independent human review is complete.
    """
    if isinstance(count, bool) or not isinstance(count, int):
        raise TypeError("count must be a positive integer")
    if count < 1:
        raise ValueError("count must be a positive integer")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise TypeError("seed must be an integer")
    targets = _case_targets(count, seed)
    phrase_hash = _phrase_bank_hash()
    source_hashes = _source_hashes()
    records: list[BenchmarkRecord] = []
    for split, _ in SPLIT_WEIGHTS:
        for slot, target in enumerate(targets[split]):
            records.append(
                _build_record(
                    case_id=f"syn-v1-{len(records) + 1:06d}",
                    global_index=len(records),
                    split=split,
                    slot=slot,
                    seed=seed,
                    target=target,
                    phrase_hash=phrase_hash,
                    source_hashes=source_hashes,
                )
            )
    return records


def write_jsonl(records: Sequence[BenchmarkRecord], path: str | Path) -> str:
    """Write canonical UTF-8 JSONL and return its SHA-256 checksum."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with destination.open("wb") as output:
        for record in records:
            payload = (
                json.dumps(
                    record.model_dump(mode="json", by_alias=True),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
                + b"\n"
            )
            output.write(payload)
            digest.update(payload)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the offline synthetic SOS benchmark")
    parser.add_argument("--seed", type=int, required=True, help="deterministic master seed")
    parser.add_argument("--count", type=int, required=True, help="positive number of examples")
    parser.add_argument("--output", type=Path, required=True, help="destination UTF-8 JSONL path")
    args = parser.parse_args()
    records = generate_dataset(count=args.count, seed=args.seed)
    checksum = write_jsonl(records, args.output)
    print(f"records={len(records)} output={args.output} sha256={checksum}")


if __name__ == "__main__":
    main()
