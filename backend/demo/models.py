"""Typed snapshots exposed by the local competition demo."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StrictInt, StrictStr

from backend.dispatch import DispatchPlan, DispatchTeam
from backend.domain.sos import ExtractedSOS, PriorityAssessment
from backend.routing import RoadStatus


class DemoModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DemoNode(DemoModel):
    node_id: StrictStr
    label: StrictStr
    x: FiniteFloat
    y: FiniteFloat


class DemoRoad(DemoModel):
    edge_id: StrictStr
    source_node_id: StrictStr
    target_node_id: StrictStr
    status: RoadStatus
    distance_meters: FiniteFloat = Field(ge=0)
    base_travel_time_seconds: FiniteFloat = Field(gt=0)
    risk_score: FiniteFloat = Field(ge=0)


class DemoReport(DemoModel):
    sos_id: StrictStr
    raw_text: StrictStr
    suggested_node_id: StrictStr | None
    suggested_required_capabilities: tuple[StrictStr, ...]
    reviewed_node_id: StrictStr | None
    reviewed_required_capabilities: tuple[StrictStr, ...] | None
    approved_dispatch_weight: StrictInt | None
    reviewed_by: StrictStr | None
    reviewed_at: datetime | None
    extraction: ExtractedSOS
    priority: PriorityAssessment


class DemoProposal(DemoModel):
    proposal_id: StrictStr
    graph_revision: StrictInt
    created_at: datetime
    plan: DispatchPlan


class DemoApproval(DemoModel):
    proposal_id: StrictStr
    graph_revision: StrictInt
    approved_by: StrictStr
    approved_at: datetime
    plan: DispatchPlan


class DemoRoadEvent(DemoModel):
    edge_id: StrictStr
    status: RoadStatus
    actor_id: StrictStr
    changed_at: datetime
    graph_revision: StrictInt


class DemoState(DemoModel):
    synthetic: bool
    mode: StrictStr
    simulation_step: StrictInt = Field(
        ge=0,
        description="Planning-only tick count; no travel or rescue outcome is simulated.",
    )
    graph_revision: StrictInt
    strategy: StrictStr
    incident_node_ids: tuple[StrictStr, ...]
    nodes: tuple[DemoNode, ...]
    roads: tuple[DemoRoad, ...]
    teams: tuple[DispatchTeam, ...]
    reports: tuple[DemoReport, ...]
    proposal: DemoProposal | None
    approved: DemoApproval | None
    road_events: tuple[DemoRoadEvent, ...]
