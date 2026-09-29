"""HTTP integration checks for the local, synthetic dispatcher demonstration."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from backend.api.app import FRONTEND_DIST, create_app
from backend.demo.models import DemoState


@pytest.fixture
def client() -> Iterator[TestClient]:
    # Each app owns its own in-memory graph, reports, and proposal sequence.
    with TestClient(create_app()) as demo_client:
        yield demo_client


def _state(response: Response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    payload = cast(dict[str, Any], response.json())
    DemoState.model_validate(payload)
    return payload


def _submit(client: TestClient, text: str, node_id: str = "north") -> dict[str, Any]:
    return _state(
        client.post(
            "/api/demo/sos", json={"text": text, "suggested_node_id": node_id}
        )
    )


def _review(
    client: TestClient,
    sos_id: str,
    *,
    node_id: str = "north",
    weight: int = 5,
    capabilities: list[str] | None = None,
) -> dict[str, Any]:
    return _state(
        client.post(
            f"/api/demo/sos/{sos_id}/review",
            json={
                "incident_node_id": node_id,
                "dispatch_weight": weight,
                "required_capabilities": capabilities or ["basic"],
                "reviewer_id": "test-reviewer",
            },
        )
    )


def test_sos_submission_exposes_evidence_and_requires_review_before_dispatch(
    client: TestClient,
) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    initial = _state(client.get("/api/demo/state"))
    assert initial["synthetic"] is True
    assert initial["reports"] == []
    assert initial["proposal"] is None
    assert initial["approved"] is None

    text = "2 people are trapped. One is unconscious. We need a boat."
    submitted = _submit(client, text)
    assert submitted["graph_revision"] == initial["graph_revision"]
    assert len(submitted["reports"]) == 1
    report = submitted["reports"][0]
    assert report["sos_id"] == "sos-001"
    assert report["raw_text"] == text
    assert report["suggested_node_id"] == "north"
    assert report["reviewed_node_id"] is None
    assert report["approved_dispatch_weight"] is None
    assert report["suggested_required_capabilities"] == ["basic", "boat", "medical"]

    extraction = report["extraction"]
    assert extraction["extractor"]["kind"] == "rule_based"
    assert extraction["facts"]["people_count"]["value"] == {"min": 2, "max": 2}
    assert extraction["facts"]["trapped"]["value"] is True
    assert extraction["facts"]["injury_reported"]["state"] == "unknown"
    assert extraction["facts"]["injury_reported"]["value"] is None
    evidence_by_id = {item["id"]: item for item in extraction["evidence"]}
    assert evidence_by_id
    for field in extraction["facts"].values():
        for candidate in field["candidates"]:
            for evidence_id in candidate["evidence_ids"]:
                span = evidence_by_id[evidence_id]["text_span"]
                assert text[span["start"] : span["end"]] == span["quote"]

    priority = report["priority"]
    assert priority["based_on"]["snapshot_id"] == extraction["extraction_id"]
    assert priority["suggested_attention"] == "immediate_review"
    assert priority["priority_weight"] == 5.0
    assert priority["requires_human_review"] is True
    assert report["approved_dispatch_weight"] is None
    assert submitted["proposal"]["plan"]["assignments"] == []
    assert set(submitted["proposal"]["plan"]["unserved_requests"][0]["reasons"]) == {
        "location_pending_review",
        "weight_pending_review",
    }
    assert client.post(
        f"/api/demo/proposals/{submitted['proposal']['proposal_id']}/approve",
        json={"dispatcher_id": "test-dispatcher"},
    ).status_code == 409


def test_review_block_replan_reject_stale_approve_and_reset(client: TestClient) -> None:
    submitted = _submit(client, "2 people are trapped. We need a boat.")
    sos_id = submitted["reports"][0]["sos_id"]
    reviewed = _review(client, sos_id, capabilities=["basic", "boat"])
    report = reviewed["reports"][0]
    assert report["reviewed_node_id"] == "north"
    assert report["reviewed_required_capabilities"] == ["basic", "boat"]
    assert report["approved_dispatch_weight"] == 5
    assert report["reviewed_by"] == "test-reviewer"
    assert report["reviewed_at"] is not None

    original = reviewed["proposal"]
    assert original["plan"]["status"] == "awaiting_human_approval"
    assert original["plan"]["unserved_requests"] == []
    assert len(original["plan"]["assignments"]) == 1
    assignment = original["plan"]["assignments"][0]
    assert assignment["request_id"] == sos_id
    assert assignment["team_id"] == "team_bravo"
    assert assignment["route"]["node_ids"][0] == "base_b"
    assert assignment["route"]["node_ids"][-1] == "north"
    assert assignment["route"]["graph_revision"] == original["graph_revision"]
    assert assignment["predicted_response_seconds"] >= assignment["eta_seconds"]
    assert assignment["predicted_arrival_at"] is not None
    assert original["plan"]["objective_value"]["weighted_pending_loss"] == 0
    assert original["plan"]["explanation_metadata"]["human_approval_required"] is True
    assert reviewed["approved"] is None

    blocked_edge = assignment["route"]["edge_ids"][0]
    blocked = _state(
        client.post(
            f"/api/demo/roads/{blocked_edge}/block", json={"actor_id": "test-dispatcher"}
        )
    )
    assert blocked["graph_revision"] == reviewed["graph_revision"] + 1
    assert next(road for road in blocked["roads"] if road["edge_id"] == blocked_edge)[
        "status"
    ] == "BLOCKED"
    assert blocked["road_events"][-1]["edge_id"] == blocked_edge
    assert blocked["road_events"][-1]["graph_revision"] == blocked["graph_revision"]
    replacement = blocked["proposal"]
    assert replacement["proposal_id"] != original["proposal_id"]
    assert replacement["graph_revision"] == blocked["graph_revision"]
    assert len(replacement["plan"]["assignments"]) == 1
    rerouted = replacement["plan"]["assignments"][0]
    assert rerouted["request_id"] == sos_id
    assert blocked_edge not in rerouted["route"]["edge_ids"]
    assert rerouted["route"]["edge_ids"] != assignment["route"]["edge_ids"]

    stale = client.post(
        f"/api/demo/proposals/{original['proposal_id']}/approve",
        json={"dispatcher_id": "test-dispatcher"},
    )
    assert stale.status_code == 409
    assert "stale" in stale.json()["detail"]
    approved = _state(
        client.post(
            f"/api/demo/proposals/{replacement['proposal_id']}/approve",
            json={"dispatcher_id": "test-dispatcher"},
        )
    )
    assert approved["approved"]["proposal_id"] == replacement["proposal_id"]
    assert approved["approved"]["graph_revision"] == blocked["graph_revision"]
    assert approved["approved"]["approved_by"] == "test-dispatcher"
    assert approved["approved"]["plan"] == replacement["plan"]

    unblocked = _state(
        client.post(
            f"/api/demo/roads/{blocked_edge}/unblock", json={"actor_id": "test-dispatcher"}
        )
    )
    assert unblocked["graph_revision"] == blocked["graph_revision"] + 1
    assert unblocked["proposal"]["proposal_id"] != replacement["proposal_id"]
    assert next(road for road in unblocked["roads"] if road["edge_id"] == blocked_edge)[
        "status"
    ] == "OPEN"
    assert client.post(
        f"/api/demo/proposals/{replacement['proposal_id']}/approve",
        json={"dispatcher_id": "test-dispatcher"},
    ).status_code == 409

    reset = _state(client.post("/api/demo/reset"))
    assert reset["graph_revision"] == submitted["graph_revision"]
    assert reset["reports"] == []
    assert reset["proposal"] is None
    assert reset["approved"] is None
    assert reset["road_events"] == []


@pytest.mark.parametrize(
    ("review", "expected_status"),
    [
        ({"required_capabilities": None}, 422),
        ({"required_capabilities": ["boat"]}, 400),
        ({"required_capabilities": ["basic", "basic"]}, 400),
        ({"required_capabilities": ["basic", "unknown"]}, 422),
        ({"incident_node_id": "base_a"}, 400),
        ({"dispatch_weight": "5"}, 422),
        ({"dispatch_weight": 0}, 422),
    ],
)
def test_invalid_review_never_changes_report(
    client: TestClient, review: dict[str, Any], expected_status: int
) -> None:
    submitted = _submit(client, "Please help at the bridge.")
    original = submitted["reports"][0]
    payload: dict[str, Any] = {
        "incident_node_id": "north",
        "dispatch_weight": 2,
        "required_capabilities": ["basic"],
        "reviewer_id": "test-reviewer",
    }
    payload.update(review)
    if review.get("required_capabilities") is None and "required_capabilities" in review:
        del payload["required_capabilities"]
    response = client.post(f"/api/demo/sos/{original['sos_id']}/review", json=payload)
    assert response.status_code == expected_status, response.text
    current = _state(client.get("/api/demo/state"))
    assert current["reports"][0] == original
    assert current["proposal"]["plan"]["assignments"] == []


def test_capability_filtering_and_missing_resource_errors(client: TestClient) -> None:
    submitted = _submit(client, "One person needs rescue at North.")
    sos_id = submitted["reports"][0]["sos_id"]
    assert client.post(
        "/api/demo/sos/does-not-exist/review",
        json={
            "incident_node_id": "north",
            "dispatch_weight": 2,
            "required_capabilities": ["basic"],
            "reviewer_id": "test-reviewer",
        },
    ).status_code == 404
    assert client.post(
        "/api/demo/roads/does-not-exist/block", json={"actor_id": "test-dispatcher"}
    ).status_code == 404

    # Each team lacks one of these specialized capabilities, so routing alone
    # cannot turn an ineligible team into an assignment.
    reviewed = _review(client, sos_id, capabilities=["basic", "medical", "boat"])
    assert reviewed["proposal"]["plan"]["assignments"] == []
    unserved = reviewed["proposal"]["plan"]["unserved_requests"]
    assert unserved == [{"request_id": sos_id, "reasons": ["no_eligible_route"]}]
    assert reviewed["proposal"]["plan"]["objective_value"]["weighted_pending_loss"] == 5


def test_reset_does_not_reuse_report_or_proposal_ids(client: TestClient) -> None:
    first = _submit(client, "One person is trapped.")
    first_sos_id = first["reports"][0]["sos_id"]
    first_review = _review(client, first_sos_id)
    first_proposal_id = first_review["proposal"]["proposal_id"]

    _state(client.post("/api/demo/reset"))
    second = _submit(client, "One person is injured.")
    second_sos_id = second["reports"][0]["sos_id"]
    second_review = _review(client, second_sos_id)
    assert second_sos_id != first_sos_id
    assert second_review["proposal"]["proposal_id"] != first_proposal_id

    stale_review = client.post(
        f"/api/demo/sos/{first_sos_id}/review",
        json={
            "incident_node_id": "north",
            "dispatch_weight": 2,
            "required_capabilities": ["basic"],
            "reviewer_id": "stale-reviewer",
        },
    )
    assert stale_review.status_code == 404
    stale_approval = client.post(
        f"/api/demo/proposals/{first_proposal_id}/approve",
        json={"dispatcher_id": "stale-dispatcher"},
    )
    assert stale_approval.status_code == 409


def test_built_demo_page_and_assets_are_served_when_present(client: TestClient) -> None:
    if not (FRONTEND_DIST / "index.html").is_file():
        pytest.skip("frontend build is not present; npm run build performs the build check")
    page = client.get("/demo")
    assert page.status_code == 200
    assert "RescueAI" in page.text
    for filename in ("app.js", "app.css"):
        assert (Path(FRONTEND_DIST) / filename).is_file()
        asset = client.get(f"/demo/static/{filename}")
        assert asset.status_code == 200
        assert asset.content
