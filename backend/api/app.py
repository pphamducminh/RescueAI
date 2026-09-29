"""FastAPI transport for the local, in-memory RescueAI competition demo."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend.api.schemas import (
    HealthResponse,
    ProposalApproval,
    RoadChange,
    SOSReview,
    SOSSubmission,
)
from backend.demo.models import DemoState
from backend.demo.service import DemoError, DemoService

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def create_app() -> FastAPI:
    """Create an isolated demo session, useful for both tests and local launch."""

    application = FastAPI(title="RescueAI demo", version="0.1.0")
    demo = DemoService()
    application.state.demo = demo
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

    @application.get("/api/demo/state", response_model=DemoState, tags=["demo"])
    def state() -> DemoState:
        return demo.state()

    @application.post("/api/demo/reset", response_model=DemoState, tags=["demo"])
    def reset() -> DemoState:
        return demo.reset()

    @application.post("/api/demo/sos", response_model=DemoState, tags=["demo"])
    def submit_sos(payload: SOSSubmission) -> DemoState:
        try:
            return demo.submit_sos(payload.text, payload.suggested_node_id)
        except DemoError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @application.post("/api/demo/sos/{sos_id}/review", response_model=DemoState, tags=["demo"])
    def review_sos(sos_id: str, payload: SOSReview) -> DemoState:
        try:
            return demo.review_sos(
                sos_id,
                incident_node_id=payload.incident_node_id,
                dispatch_weight=payload.dispatch_weight,
                required_capabilities=payload.required_capabilities,
                reviewer_id=payload.reviewer_id,
            )
        except DemoError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @application.post(
        "/api/demo/roads/{edge_id}/block", response_model=DemoState, tags=["demo"]
    )
    def block_road(edge_id: str, payload: RoadChange) -> DemoState:
        try:
            return demo.change_road(edge_id, blocked=True, actor_id=payload.actor_id)
        except DemoError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @application.post(
        "/api/demo/roads/{edge_id}/unblock", response_model=DemoState, tags=["demo"]
    )
    def unblock_road(edge_id: str, payload: RoadChange) -> DemoState:
        try:
            return demo.change_road(edge_id, blocked=False, actor_id=payload.actor_id)
        except DemoError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @application.post(
        "/api/demo/proposals/{proposal_id}/approve",
        response_model=DemoState,
        tags=["demo"],
    )
    def approve_proposal(proposal_id: str, payload: ProposalApproval) -> DemoState:
        try:
            return demo.approve_proposal(proposal_id, dispatcher_id=payload.dispatcher_id)
        except DemoError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    return application


app = create_app()
