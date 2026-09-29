"""HTTP request schemas for the local demo API."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class DemoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SOSSubmission(DemoRequest):
    text: StrictStr = Field(min_length=1, max_length=2000)
    suggested_node_id: StrictStr | None = None


class SOSReview(DemoRequest):
    incident_node_id: StrictStr = Field(min_length=1)
    dispatch_weight: StrictInt = Field(ge=1, le=5)
    required_capabilities: tuple[Literal["basic", "medical", "boat"], ...] = Field(min_length=1)
    reviewer_id: StrictStr = Field(min_length=1)


class RoadChange(DemoRequest):
    actor_id: StrictStr = Field(min_length=1)


class ProposalApproval(DemoRequest):
    dispatcher_id: StrictStr = Field(min_length=1)
