"""FastAPI transport for the local, in-memory RescueAI competition demo."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend.api.schemas import (
    HealthResponse,
    ProposalApproval,
    RoadChange,
    SOSReview,
    SOSSubmission,
)
from backend.demo.models import DemoProposal, DemoReport, DemoState
from backend.demo.service import DemoError, DemoService
from backend.dispatch import DispatchTeam

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def create_app() -> FastAPI:
    """Create an isolated demo session, useful for both tests and local launch."""

    application = FastAPI(
        title="RescueAI API",
        version="0.1.0",
        description=(
            "Synthetic, in-memory decision-support API. SOS extraction and priority "
            "suggestions are rule-based. Dispatch requires explicit review; simulation "
            "steps refresh plans without moving teams or recording rescue outcomes."
        ),
    )
    demo = DemoService()
    application.state.demo = demo

    @application.exception_handler(DemoError)
    async def demo_error_handler(_request: Request, exc: DemoError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    application.mount(
        "/demo/static",
        StaticFiles(directory=FRONTEND_DIST, check_dir=False),
        name="demo-static",
    )

    @application.get("/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse()

    @application.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse(url="/demo")

    @application.get("/demo", include_in_schema=False)
    def demo_page() -> FileResponse:
        index = FRONTEND_DIST / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=503, detail="build frontend with npm run build")
        return FileResponse(index)

    @application.get(
        "/system/state",
        response_model=DemoState,
        tags=["system"],
        summary="Read the current synthetic system state",
    )
    @application.get("/api/demo/state", response_model=DemoState, tags=["demo"])
    def state() -> DemoState:
        return demo.state()

    @application.get(
        "/sos", response_model=tuple[DemoReport, ...], tags=["sos"], summary="List SOS reports"
    )
    def list_sos() -> tuple[DemoReport, ...]:
        return demo.state().reports

    @application.get(
        "/teams",
        response_model=tuple[DispatchTeam, ...],
        tags=["teams"],
        summary="List rescue teams",
    )
    def list_teams() -> tuple[DispatchTeam, ...]:
        return demo.state().teams

    @application.post(
        "/simulation/reset",
        response_model=DemoState,
        tags=["simulation"],
        summary="Reset the synthetic local session",
    )
    @application.post("/api/demo/reset", response_model=DemoState, tags=["demo"])
    def reset() -> DemoState:
        return demo.reset()

    @application.post(
        "/simulation/step",
        response_model=DemoState,
        tags=["simulation"],
        summary="Advance one planning tick",
        description=(
            "Increment the planning-step counter and recompute the current one-wave "
            "proposal. This does not move teams, advance a travel clock, or mark SOS "
            "requests as served."
        ),
    )
    def step() -> DemoState:
        return demo.step()

    @application.post(
        "/sos",
        response_model=DemoState,
        tags=["sos"],
        summary="Submit an SOS report",
        description=(
            "Extract reported claims and suggest priority offline. The submitted "
            "incident node is only a suggestion; review is required before routing."
        ),
    )
    @application.post("/api/demo/sos", response_model=DemoState, tags=["demo"])
    def submit_sos(payload: SOSSubmission) -> DemoState:
        return demo.submit_sos(payload.text, payload.suggested_node_id)

    @application.post(
        "/sos/{sos_id}/review",
        response_model=DemoState,
        tags=["sos"],
        summary="Accept reviewed dispatch inputs for an SOS",
        description=(
            "A dispatcher must explicitly accept the incident node, required team "
            "capabilities, and dispatch weight before the request is routable."
        ),
    )
    @application.post("/api/demo/sos/{sos_id}/review", response_model=DemoState, tags=["demo"])
    def review_sos(sos_id: str, payload: SOSReview) -> DemoState:
        return demo.review_sos(
            sos_id,
            incident_node_id=payload.incident_node_id,
            dispatch_weight=payload.dispatch_weight,
            required_capabilities=payload.required_capabilities,
            reviewer_id=payload.reviewer_id,
        )

    @application.post(
        "/dispatch/plan",
        response_model=DemoProposal,
        tags=["dispatch"],
        summary="Recompute a one-wave dispatch proposal",
        description=(
            "Returns a proposal awaiting human approval. Unreviewed or unreachable "
            "requests remain unserved with explicit reasons."
        ),
    )
    def plan() -> DemoProposal:
        return demo.plan()

    @application.post(
        "/roads/{edge_id}/block",
        response_model=DemoState,
        tags=["roads"],
        summary="Block a directed road and automatically replan",
    )
    @application.post(
        "/api/demo/roads/{edge_id}/block", response_model=DemoState, tags=["demo"]
    )
    def block_road(edge_id: str, payload: RoadChange) -> DemoState:
        return demo.change_road(edge_id, blocked=True, actor_id=payload.actor_id)

    @application.post(
        "/roads/{edge_id}/unblock",
        response_model=DemoState,
        tags=["roads"],
        summary="Unblock a directed road and automatically replan",
    )
    @application.post(
        "/api/demo/roads/{edge_id}/unblock", response_model=DemoState, tags=["demo"]
    )
    def unblock_road(edge_id: str, payload: RoadChange) -> DemoState:
        return demo.change_road(edge_id, blocked=False, actor_id=payload.actor_id)

    @application.post(
        "/dispatch/proposals/{proposal_id}/approve",
        response_model=DemoState,
        tags=["dispatch"],
        summary="Record dispatcher approval of the current proposal",
        description="Rejects stale proposals and proposals without assignments.",
    )
    @application.post(
        "/api/demo/proposals/{proposal_id}/approve",
        response_model=DemoState,
        tags=["demo"],
    )
    def approve_proposal(proposal_id: str, payload: ProposalApproval) -> DemoState:
        return demo.approve_proposal(proposal_id, dispatcher_id=payload.dispatcher_id)

    return application


app = create_app()
