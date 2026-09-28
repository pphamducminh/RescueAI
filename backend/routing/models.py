"""Validated, immutable inputs and outputs for deterministic road routing."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Self, TypeAlias

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    StrictInt,
    StrictStr,
    model_validator,
)


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("identifier must not be blank")
    return value


Identifier: TypeAlias = Annotated[StrictStr, Field(min_length=1), AfterValidator(_nonblank)]
NonNegativeFloat: TypeAlias = Annotated[FiniteFloat, Field(strict=True, ge=0)]
PositiveFloat: TypeAlias = Annotated[FiniteFloat, Field(strict=True, gt=0)]


class RoutingModel(BaseModel):
    """Reject unknown fields and keep graph snapshots immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class RoadStatus(StrEnum):
    OPEN = "OPEN"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"


class RoutingCostConfig(RoutingModel):
    """Explicit seconds per risk-score unit and degraded travel-time factor."""

    risk_penalty_seconds_per_unit: NonNegativeFloat
    degraded_time_multiplier: Annotated[FiniteFloat, Field(strict=True, ge=1)]


class Road(RoutingModel):
    """One directed road edge; add a reverse edge for two-way travel."""

    edge_id: Identifier
    source_node_id: Identifier
    target_node_id: Identifier
    distance_meters: NonNegativeFloat
    base_travel_time_seconds: PositiveFloat
    risk_score: NonNegativeFloat
    status: RoadStatus
    last_updated: AwareDatetime

    @model_validator(mode="after")
    def check_endpoints(self) -> Self:
        if self.source_node_id == self.target_node_id:
            raise ValueError("a road must connect distinct nodes")
        return self

    def effective_travel_time_seconds(self, config: RoutingCostConfig) -> float:
        """Travel time with degraded-road slowdown, excluding risk preference."""

        if self.status is RoadStatus.BLOCKED:
            raise ValueError("blocked roads have no traversable travel time")
        multiplier = config.degraded_time_multiplier if self.status is RoadStatus.DEGRADED else 1.0
        return self.base_travel_time_seconds * multiplier

    def effective_cost_seconds(self, config: RoutingCostConfig) -> float:
        """Routing objective: adjusted travel time plus configurable risk penalty."""

        return (
            self.effective_travel_time_seconds(config)
            + config.risk_penalty_seconds_per_unit * self.risk_score
        )


class SafeRoute(RoutingModel):
    """Route calculated against one graph revision, with objective and ETA apart."""

    node_ids: tuple[Identifier, ...]
    edge_ids: tuple[Identifier, ...]
    travel_time_seconds: NonNegativeFloat
    distance_meters: NonNegativeFloat
    effective_cost_seconds: NonNegativeFloat
    risk_score_sum: NonNegativeFloat
    graph_revision: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def check_path_shape(self) -> Self:
        if not self.node_ids or len(self.edge_ids) != len(self.node_ids) - 1:
            raise ValueError("a route needs one node more than edges")
        return self


class GraphSnapshot(RoutingModel):
    """Stable, sorted view of current topology and cost configuration."""

    revision: StrictInt = Field(ge=0)
    node_ids: tuple[Identifier, ...]
    roads: tuple[Road, ...]
    cost_config: RoutingCostConfig


def require_not_older(updated_at: datetime, previous: datetime) -> None:
    """Reject stale road updates without silently rolling back edge state."""

    if updated_at.tzinfo is None or updated_at.utcoffset() is None:
        raise ValueError("updated_at must include a timezone")
    if updated_at < previous:
        raise ValueError("road update timestamp precedes current state")
