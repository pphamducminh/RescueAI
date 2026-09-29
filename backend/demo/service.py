"""Small in-memory bridge from SOS text to reviewable dispatch proposals.

The map, teams, and rules are synthetic demo inputs. Approval records a human
decision; this service does not move teams, contact responders, or persist data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock

from backend.dispatch import (
    DispatchRequest,
    DispatchTeam,
    build_dispatch_state,
    get_strategy,
)
from backend.domain.sos import (
    AssistanceTag,
    ExtractedSOS,
    FieldState,
    PriorityAssessment,
    SOSInput,
)
from backend.extraction.rules import RuleBasedSOSExtractor
from backend.priority.rules import RuleBasedPriorityAssessor
from backend.routing import DynamicRoadGraph, Road, RoadStatus, RoutingCostConfig

from .models import (
    DemoApproval,
    DemoNode,
    DemoProposal,
    DemoReport,
    DemoRoad,
    DemoRoadEvent,
    DemoState,
)


class DemoError(ValueError):
    """A user-facing demo operation error with an HTTP status code."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class _StoredReport:
    source: SOSInput
    suggested_node_id: str | None
    extraction: ExtractedSOS
    priority: PriorityAssessment
    suggested_required_capabilities: frozenset[str]
    reviewed_node_id: str | None = None
    reviewed_required_capabilities: frozenset[str] | None = None
    approved_dispatch_weight: int | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None


NODES = (
    DemoNode(node_id="base_a", label="Team Alpha", x=90.0, y=160.0),
    DemoNode(node_id="base_b", label="Team Bravo", x=90.0, y=430.0),
    DemoNode(node_id="junction", label="Junction", x=300.0, y=290.0),
    DemoNode(node_id="north", label="North district", x=515.0, y=125.0),
    DemoNode(node_id="east", label="East district", x=545.0, y=290.0),
    DemoNode(node_id="south", label="South district", x=500.0, y=455.0),
)
INCIDENT_NODE_IDS = frozenset({"north", "east", "south"})
ROAD_LINKS = (
    ("base_a", "junction", 90.0, 900.0, 0.05),
    ("base_b", "junction", 130.0, 1300.0, 0.05),
    ("junction", "north", 80.0, 800.0, 0.2),
    ("junction", "east", 115.0, 1150.0, 0.1),
    ("junction", "south", 150.0, 1500.0, 0.15),
    ("north", "east", 105.0, 1050.0, 0.05),
    ("east", "south", 110.0, 1100.0, 0.05),
    ("base_a", "north", 260.0, 2600.0, 0.1),
    ("base_b", "south", 220.0, 2200.0, 0.05),
)
STRATEGY_NAME = "rescueai_optimizer"
WEIGHT_POLICY_VERSION = "demo_dispatcher_approved_weight_v1"


class DemoService:
    """One local session; all mutations produce a fresh proposal if SOS exist."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._extractor = RuleBasedSOSExtractor()
        self._assessor = RuleBasedPriorityAssessor()
        # Never reuse IDs within this API process, including after reset. Old
        # browser tabs must not target a new report or proposal by accident.
        self._next_sos_number = 1
        self._next_proposal_number = 1
        self.reset()

    def reset(self) -> DemoState:
        with self._lock:
            self._graph = DynamicRoadGraph(
                RoutingCostConfig(
                    risk_penalty_seconds_per_unit=20.0,
                    degraded_time_multiplier=1.5,
                )
            )
            at = datetime(2026, 1, 1, tzinfo=UTC)
            for source, target, seconds, distance, risk in ROAD_LINKS:
                for start, end in ((source, target), (target, source)):
                    self._graph.add_road(
                        Road(
                            edge_id=f"{start}_to_{end}",
                            source_node_id=start,
                            target_node_id=end,
                            distance_meters=distance,
                            base_travel_time_seconds=seconds,
                            risk_score=risk,
                            status=RoadStatus.OPEN,
                            last_updated=at,
                        )
                    )
            self._teams = (
                DispatchTeam(
                    team_id="team_alpha",
                    graph_node_id="base_a",
                    available=True,
                    capabilities=frozenset({"basic", "medical"}),
                ),
                DispatchTeam(
                    team_id="team_bravo",
                    graph_node_id="base_b",
                    available=True,
                    capabilities=frozenset({"basic", "boat"}),
                ),
            )
            self._reports: dict[str, _StoredReport] = {}
            self._proposal: DemoProposal | None = None
            self._approved: DemoApproval | None = None
            self._road_events: list[DemoRoadEvent] = []
            return self._view()

    def state(self) -> DemoState:
        with self._lock:
            return self._view()

    def submit_sos(self, text: str, suggested_node_id: str | None) -> DemoState:
        with self._lock:
            if not text.strip():
                raise DemoError("SOS text must not be blank")
            if suggested_node_id is not None and suggested_node_id not in INCIDENT_NODE_IDS:
                raise DemoError("suggested incident node is not on the demo map")
            sos_id = f"sos-{self._next_sos_number:03d}"
            self._next_sos_number += 1
            source = SOSInput(
                schema_version="1.0",
                sos_id=sos_id,
                input_revision=1,
                raw_text=text,
                image_ref=None,
                submitted_location=None,
                received_at=datetime.now(UTC),
                reported_event_at=None,
            )
            extraction = self._extractor.extract(source)
            priority = self._assessor.assess(extraction)
            priority.assert_matches_extraction(extraction)
            self._reports[sos_id] = _StoredReport(
                source=source,
                suggested_node_id=suggested_node_id,
                extraction=extraction,
                priority=priority,
                suggested_required_capabilities=self._suggested_capabilities(extraction),
            )
            self._recompute()
            return self._view()

    def review_sos(
        self,
        sos_id: str,
        *,
        incident_node_id: str,
        dispatch_weight: int,
        required_capabilities: tuple[str, ...],
        reviewer_id: str,
    ) -> DemoState:
        with self._lock:
            report = self._reports.get(sos_id)
            if report is None:
                raise DemoError("SOS report does not exist", status_code=404)
            if incident_node_id not in INCIDENT_NODE_IDS:
                raise DemoError("accepted incident node is not on the demo map")
            if not 1 <= dispatch_weight <= 5:
                raise DemoError("demo dispatch weight must be between 1 and 5")
            if not reviewer_id.strip():
                raise DemoError("reviewer ID must not be blank")
            approved_capabilities = frozenset(required_capabilities)
            if (
                not required_capabilities
                or len(approved_capabilities) != len(required_capabilities)
                or "basic" not in approved_capabilities
                or not approved_capabilities.issubset({"basic", "medical", "boat"})
            ):
                raise DemoError("reviewed capabilities must include basic and use known values")
            report.reviewed_node_id = incident_node_id
            report.reviewed_required_capabilities = approved_capabilities
            report.approved_dispatch_weight = dispatch_weight
            report.reviewed_by = reviewer_id
            report.reviewed_at = datetime.now(UTC)
            self._recompute()
            return self._view()

    def change_road(self, edge_id: str, *, blocked: bool, actor_id: str) -> DemoState:
        with self._lock:
            if not actor_id.strip():
                raise DemoError("actor ID must not be blank")
            try:
                road = self._graph.get_road(edge_id)
            except KeyError as exc:
                raise DemoError("road does not exist", status_code=404) from exc
            requested = RoadStatus.BLOCKED if blocked else RoadStatus.OPEN
            if road.status is requested:
                raise DemoError("road already has the requested status", status_code=409)
            at = datetime.now(UTC)
            if blocked:
                self._graph.block_road(edge_id, at)
            else:
                self._graph.unblock_road(edge_id, at)
            self._road_events.append(
                DemoRoadEvent(
                    edge_id=edge_id,
                    status=requested,
                    actor_id=actor_id,
                    changed_at=at,
                    graph_revision=self._graph.revision,
                )
            )
            self._recompute()
            return self._view()

    def approve_proposal(self, proposal_id: str, *, dispatcher_id: str) -> DemoState:
        with self._lock:
            if not dispatcher_id.strip():
                raise DemoError("dispatcher ID must not be blank")
            proposal = self._proposal
            if proposal is None or proposal.proposal_id != proposal_id:
                raise DemoError("proposal is stale or does not exist", status_code=409)
            if proposal.graph_revision != self._graph.revision:
                raise DemoError("proposal uses an outdated road graph", status_code=409)
            if not proposal.plan.assignments:
                raise DemoError("proposal has no assignments to approve", status_code=409)
            self._approved = DemoApproval(
                proposal_id=proposal.proposal_id,
                graph_revision=proposal.graph_revision,
                approved_by=dispatcher_id,
                approved_at=datetime.now(UTC),
                plan=proposal.plan,
            )
            return self._view()

    @staticmethod
    def _suggested_capabilities(extraction: ExtractedSOS) -> frozenset[str]:
        facts = extraction.facts
        suggested = {"basic"}
        assistance = facts.requested_assistance
        if (
            assistance.state is FieldState.SUPPORTED
            and assistance.value is not None
            and AssistanceTag.BOAT in assistance.value
        ):
            suggested.add("boat")
        if (
            facts.injury_reported.state is FieldState.SUPPORTED
            and facts.injury_reported.value is True
        ) or (
            facts.urgent_signs.state is FieldState.SUPPORTED
            and bool(facts.urgent_signs.value)
        ):
            suggested.add("medical")
        return frozenset(suggested)

    def _dispatch_requests(self) -> tuple[DispatchRequest, ...]:
        requests: list[DispatchRequest] = []
        for sos_id in sorted(self._reports):
            report = self._reports[sos_id]
            requests.append(
                DispatchRequest(
                    request_id=sos_id,
                    received_at=report.source.received_at,
                    incident_node_id=report.reviewed_node_id,
                    routing_anchor_accepted=report.reviewed_node_id is not None,
                    required_capabilities=(
                        report.reviewed_required_capabilities or frozenset({"basic"})
                    ),
                    dispatch_weight=report.approved_dispatch_weight,
                    # Required by the shared dispatch schema, unused by this demo's solver.
                    evaluation_weight=1,
                    critical_cue=False,
                )
            )
        return tuple(requests)

    def _recompute(self) -> None:
        if not self._reports:
            self._proposal = None
            return
        state = build_dispatch_state(
            scenario_id="local-demo-session",
            decision_time=datetime.now(UTC),
            requests=self._dispatch_requests(),
            teams=self._teams,
            graph=self._graph,
            weight_policy_version=WEIGHT_POLICY_VERSION,
        )
        plan = get_strategy(STRATEGY_NAME).solve(state)
        self._proposal = DemoProposal(
            proposal_id=f"proposal-{self._next_proposal_number:03d}",
            graph_revision=state.graph_revision,
            created_at=datetime.now(UTC),
            plan=plan,
        )
        self._next_proposal_number += 1

    def _view(self) -> DemoState:
        graph_state = self._graph.get_state()
        return DemoState(
            synthetic=True,
            mode="local_rule_based_in_memory_demo",
            graph_revision=graph_state.revision,
            strategy=STRATEGY_NAME,
            incident_node_ids=tuple(sorted(INCIDENT_NODE_IDS)),
            nodes=NODES,
            roads=tuple(
                DemoRoad(
                    edge_id=road.edge_id,
                    source_node_id=road.source_node_id,
                    target_node_id=road.target_node_id,
                    status=road.status,
                    distance_meters=road.distance_meters,
                    base_travel_time_seconds=road.base_travel_time_seconds,
                    risk_score=road.risk_score,
                )
                for road in graph_state.roads
            ),
            teams=self._teams,
            reports=tuple(
                DemoReport(
                    sos_id=sos_id,
                    raw_text=report.source.raw_text,
                    suggested_node_id=report.suggested_node_id,
                    suggested_required_capabilities=tuple(
                        sorted(report.suggested_required_capabilities)
                    ),
                    reviewed_node_id=report.reviewed_node_id,
                    reviewed_required_capabilities=(
                        tuple(sorted(report.reviewed_required_capabilities))
                        if report.reviewed_required_capabilities is not None
                        else None
                    ),
                    approved_dispatch_weight=report.approved_dispatch_weight,
                    reviewed_by=report.reviewed_by,
                    reviewed_at=report.reviewed_at,
                    extraction=report.extraction,
                    priority=report.priority,
                )
                for sos_id, report in sorted(self._reports.items())
            ),
            proposal=self._proposal,
            approved=self._approved,
            road_events=tuple(self._road_events),
        )
