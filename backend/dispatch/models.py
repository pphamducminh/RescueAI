"""Immutable one-wave dispatch inputs, proposals, and objective accounting."""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Annotated, Self, TypeAlias

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    JsonValue,
    StrictBool,
    StrictInt,
    StrictStr,
    field_serializer,
    model_validator,
)

from backend.routing import SafeRoute


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("identifier must not be blank")
    return value


Identifier: TypeAlias = Annotated[StrictStr, Field(min_length=1), AfterValidator(_nonblank)]
PositiveInt: TypeAlias = Annotated[StrictInt, Field(ge=1)]
NonNegativeInt: TypeAlias = Annotated[StrictInt, Field(ge=0)]
NonNegativeFloat: TypeAlias = Annotated[FiniteFloat, Field(strict=True, ge=0)]


class DispatchModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class DispatchRequest(DispatchModel):
    """Pending request with explicitly reviewed routing/weight decisions."""

    request_id: Identifier
    received_at: AwareDatetime
    incident_node_id: Identifier | None = Field(...)
    routing_anchor_accepted: StrictBool
    required_capabilities: frozenset[Identifier]
    dispatch_weight: PositiveInt | None = Field(...)
    evaluation_weight: PositiveInt
    critical_cue: StrictBool

    @field_serializer("required_capabilities", when_used="json")
    def serialize_required_capabilities(self, value: frozenset[str]) -> list[str]:
        """Keep persisted scenario snapshots stable across Python hash seeds."""

        return sorted(value)

    @model_validator(mode="after")
    def check_anchor(self) -> Self:
        if self.routing_anchor_accepted and self.incident_node_id is None:
            raise ValueError("accepted routing anchor requires an incident node")
        return self


class DispatchTeam(DispatchModel):
    """One response team; a wave can assign it to at most one request."""

    team_id: Identifier
    graph_node_id: Identifier
    available: StrictBool
    capabilities: frozenset[Identifier]

    @field_serializer("capabilities", when_used="json")
    def serialize_capabilities(self, value: frozenset[str]) -> list[str]:
        return sorted(value)


class FeasiblePair(DispatchModel):
    """One eligible team/request connection, with a retained graph route."""

    request_id: Identifier
    team_id: Identifier
    route: SafeRoute
    eta_seconds: NonNegativeInt

    @model_validator(mode="after")
    def check_eta(self) -> Self:
        if self.eta_seconds != math.ceil(self.route.travel_time_seconds):
            raise ValueError("integer planning ETA must round route travel time upward")
        return self


class DispatchState(DispatchModel):
    """One frozen routing/eligibility snapshot shared by every strategy."""

    scenario_id: Identifier
    decision_time: AwareDatetime
    graph_revision: NonNegativeInt
    weight_policy_version: Identifier
    requests: tuple[DispatchRequest, ...]
    teams: tuple[DispatchTeam, ...]
    feasible_pairs: tuple[FeasiblePair, ...]

    @model_validator(mode="after")
    def check_snapshot(self) -> Self:
        requests = {item.request_id: item for item in self.requests}
        teams = {item.team_id: item for item in self.teams}
        if len(requests) != len(self.requests) or len(teams) != len(self.teams):
            raise ValueError("request and team IDs must be unique")
        if any(item.received_at > self.decision_time for item in self.requests):
            raise ValueError("a pending request cannot arrive after the decision time")
        seen_pairs: set[tuple[str, str]] = set()
        for pair in self.feasible_pairs:
            key = (pair.request_id, pair.team_id)
            if key in seen_pairs:
                raise ValueError("feasible team/request pairs must be unique")
            seen_pairs.add(key)
            request = requests.get(pair.request_id)
            team = teams.get(pair.team_id)
            if request is None or team is None:
                raise ValueError("feasible pair references an absent request or team")
            if not request.routing_anchor_accepted or request.incident_node_id is None:
                raise ValueError("feasible pair needs an accepted incident routing anchor")
            if not team.available or not request.required_capabilities.issubset(team.capabilities):
                raise ValueError("feasible pair violates availability or capability")
            if (
                pair.route.graph_revision != self.graph_revision
                or pair.route.node_ids[0] != team.graph_node_id
                or pair.route.node_ids[-1] != request.incident_node_id
            ):
                raise ValueError("feasible pair route must match team, request and graph")
        return self


class UnservedReason(StrEnum):
    LOCATION_PENDING_REVIEW = "location_pending_review"
    WEIGHT_PENDING_REVIEW = "weight_pending_review"
    NO_ELIGIBLE_ROUTE = "no_eligible_route"
    CAPACITY_SATURATED = "capacity_saturated"


class UnservedRequest(DispatchModel):
    request_id: Identifier
    reasons: tuple[UnservedReason, ...] = Field(min_length=1)


class DispatchAssignment(DispatchModel):
    request_id: Identifier
    team_id: Identifier
    route: SafeRoute
    eta_seconds: NonNegativeInt
    predicted_arrival_at: AwareDatetime
    predicted_response_seconds: NonNegativeFloat
    explanation: Identifier


class ObjectiveValue(DispatchModel):
    """Comparable lexicographic P/Q values and their exact scalar encoding."""

    weighted_pending_loss: NonNegativeInt
    weighted_travel_seconds: NonNegativeInt
    dominance_constant: PositiveInt
    scalar_cost: NonNegativeInt

    @model_validator(mode="after")
    def check_scalar(self) -> Self:
        if self.scalar_cost != (
            self.dominance_constant * self.weighted_pending_loss
            + self.weighted_travel_seconds
        ):
            raise ValueError("scalar cost must equal K*P+Q")
        return self


class PlanStatus(StrEnum):
    AWAITING_HUMAN_APPROVAL = "awaiting_human_approval"


class DispatchPlan(DispatchModel):
    """A proposal, never an operationally approved dispatch action."""

    strategy_name: Identifier
    scenario_id: Identifier
    graph_revision: NonNegativeInt
    status: PlanStatus
    assignments: tuple[DispatchAssignment, ...]
    unserved_requests: tuple[UnservedRequest, ...]
    objective_value: ObjectiveValue
    explanation_metadata: dict[str, JsonValue]

    @model_validator(mode="after")
    def check_unique_assignments(self) -> Self:
        request_ids = [item.request_id for item in self.assignments]
        team_ids = [item.team_id for item in self.assignments]
        unserved_ids = [item.request_id for item in self.unserved_requests]
        if len(request_ids) != len(set(request_ids)) or len(team_ids) != len(set(team_ids)):
            raise ValueError("each request and team may be assigned at most once")
        if len(unserved_ids) != len(set(unserved_ids)) or set(request_ids) & set(unserved_ids):
            raise ValueError("assigned and unserved requests must be disjoint and unique")
        return self
