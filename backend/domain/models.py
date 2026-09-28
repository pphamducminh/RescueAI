"""Domain contracts; these types do not imply implemented workflows."""

from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class ContractModel(BaseModel):
    """Reject accidental extra fields in boundary data."""

    model_config = ConfigDict(extra="forbid")


class RescueTeam(ContractModel):
    team_id: str
    graph_node_id: str
    capabilities: set[str] = Field(default_factory=set)
    available: bool = True
    capacity: int | None = Field(default=None, ge=0)
    capacity_definition: str | None = None

    @model_validator(mode="after")
    def require_capacity_definition(self) -> Self:
        """Prevent an unspecified meaning for a supplied capacity value."""
        if self.capacity is not None and not self.capacity_definition:
            raise ValueError("capacity_definition is required when capacity is supplied")
        return self


class RoadEdge(ContractModel):
    edge_id: str
    source_node_id: str
    target_node_id: str
    travel_time_seconds: float = Field(gt=0)
    distance_meters: float = Field(ge=0)
    risk: float | None = Field(default=None, ge=0)
    blocked: bool = False


class RoadEvent(ContractModel):
    edge_id: str
    occurred_at: AwareDatetime
    blocked: bool


class Route(ContractModel):
    node_ids: list[str]
    edge_ids: list[str]
    travel_time_seconds: float = Field(ge=0)
    distance_meters: float = Field(ge=0)


class ProposedAssignment(ContractModel):
    report_id: str
    team_id: str
    route: Route
    predicted_arrival_at: AwareDatetime
    reason: str


class ProposalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalAction(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class AssignmentProposal(ContractModel):
    proposal_id: str
    scenario_state_id: str
    policy_name: str
    assignments: list[ProposedAssignment]
    reasons: list[str] = Field(default_factory=list)
    status: ProposalStatus = ProposalStatus.PENDING


class ApprovalEvent(ContractModel):
    proposal_id: str
    action: ApprovalAction
    actor_id: str
    occurred_at: AwareDatetime
