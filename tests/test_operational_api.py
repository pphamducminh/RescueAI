"""HTTP integration checks for the documented root-level operational API."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from backend.api.app import create_app
from backend.demo.models import DemoProposal, DemoReport, DemoState
from backend.dispatch import DispatchPlan, DispatchTeam


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as api_client:
        yield api_client


def _json_object(response: Response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _state(response: Response) -> dict[str, Any]:
    payload = _json_object(response)
    DemoState.model_validate(payload)
    return payload


def _submit(client: TestClient, text: str, node_id: str) -> dict[str, Any]:
    return _state(client.post("/sos", json={"text": text, "suggested_node_id": node_id}))


def _review(
    client: TestClient,
    sos_id: str,
    node_id: str,
    weight: int,
    capabilities: list[str],
) -> dict[str, Any]:
    return _state(
        client.post(
            f"/sos/{sos_id}/review",
            json={
                "incident_node_id": node_id,
                "dispatch_weight": weight,
                "required_capabilities": capabilities,
                "reviewer_id": "integration-reviewer",
            },
        )
    )


def _plan(client: TestClient) -> dict[str, Any]:
    payload = _json_object(client.post("/dispatch/plan"))
    DemoProposal.model_validate(payload)
    return payload


def test_resources_and_openapi_documentation(client: TestClient) -> None:
    state = _state(client.get("/system/state"))
    assert state["synthetic"] is True
    assert state["reports"] == []
    assert state["simulation_step"] == 0

    teams_response = client.get("/teams")
    assert teams_response.status_code == 200
    teams = cast(list[dict[str, Any]], teams_response.json())
    assert len(teams) == 2
    for team in teams:
        DispatchTeam.model_validate(team)

    sos_response = client.get("/sos")
    assert sos_response.status_code == 200
    assert sos_response.json() == []

    submitted = _submit(client, "Two people are trapped at North.", "north")
    assert submitted["simulation_step"] == 0
    reports = cast(list[dict[str, Any]], client.get("/sos").json())
    assert len(reports) == 1
    DemoReport.model_validate(reports[0])
    assert reports[0]["sos_id"] == submitted["reports"][0]["sos_id"]
    assert reports[0]["extraction"]["extractor"]["kind"] == "rule_based"

    docs = client.get("/docs")
    assert docs.status_code == 200
    schema = _json_object(client.get("/openapi.json"))
    paths = schema["paths"]
    for path, method in (
        ("/sos", "post"),
        ("/sos", "get"),
        ("/sos/{sos_id}/review", "post"),
        ("/teams", "get"),
        ("/dispatch/plan", "post"),
        ("/dispatch/proposals/{proposal_id}/approve", "post"),
        ("/roads/{edge_id}/block", "post"),
        ("/roads/{edge_id}/unblock", "post"),
        ("/simulation/reset", "post"),
        ("/simulation/step", "post"),
        ("/system/state", "get"),
    ):
        assert method in paths[path]


def test_sos_plan_block_recompute_and_validate_routes(client: TestClient) -> None:
    first = _submit(client, "Two people are trapped at North.", "north")
    first_id = first["reports"][0]["sos_id"]
    second = _submit(client, "One person needs a boat at South.", "south")
    second_id = second["reports"][1]["sos_id"]
    assert second_id != first_id

    pending_plan = _plan(client)
    assert pending_plan["plan"]["assignments"] == []
    assert {item["request_id"] for item in pending_plan["plan"]["unserved_requests"]} == {
        first_id,
        second_id,
    }

    _review(client, first_id, "north", 5, ["basic"])
    _review(client, second_id, "south", 2, ["basic", "boat"])
    original = _plan(client)
    original_plan = DispatchPlan.model_validate(original["plan"])
    assert len(original_plan.assignments) == 2
    assert not original_plan.unserved_requests
    assert {item.request_id for item in original_plan.assignments} == {first_id, second_id}

    # This edge is chosen from the actual computed route, not assumed from the map.
    edge_to_block = original_plan.assignments[0].route.edge_ids[0]
    blocked_state = _state(
        client.post(f"/roads/{edge_to_block}/block", json={"actor_id": "dispatcher-1"})
    )
    assert blocked_state["graph_revision"] == original["graph_revision"] + 1
    road_status = {
        road["edge_id"]: road["status"] for road in blocked_state["roads"]
    }
    assert road_status[edge_to_block] == "BLOCKED"
    # Road updates automatically replan; an explicit plan request also remains available.
    automatic = DemoProposal.model_validate(blocked_state["proposal"])
    assert automatic.proposal_id != original["proposal_id"]
    assert automatic.graph_revision == blocked_state["graph_revision"]

    updated = _plan(client)
    updated_plan = DispatchPlan.model_validate(updated["plan"])
    assert updated["graph_revision"] == blocked_state["graph_revision"]
    assert updated["proposal_id"] != original["proposal_id"]
    assert {item.request_id for item in updated_plan.assignments} == {first_id, second_id}
    assert not updated_plan.unserved_requests
    current_state = _state(client.get("/system/state"))
    assert current_state["proposal"] == updated
    assert _state(client.get("/api/demo/state")) == current_state
    roads_by_id = {road["edge_id"]: road for road in current_state["roads"]}
    teams_by_id = {team["team_id"]: team for team in current_state["teams"]}
    reports_by_id = {report["sos_id"]: report for report in current_state["reports"]}
    for assignment in updated_plan.assignments:
        route = assignment.route
        team = teams_by_id[assignment.team_id]
        report = reports_by_id[assignment.request_id]
        assert route.graph_revision == updated_plan.graph_revision
        assert route.node_ids[0] == team["graph_node_id"]
        assert route.node_ids[-1] == report["reviewed_node_id"]
        assert set(report["reviewed_required_capabilities"]).issubset(team["capabilities"])
        assert len(route.edge_ids) == len(route.node_ids) - 1
        assert edge_to_block not in route.edge_ids
        for position, edge_id in enumerate(route.edge_ids):
            road = roads_by_id[edge_id]
            assert road["status"] != "BLOCKED"
            assert road["source_node_id"] == route.node_ids[position]
            assert road["target_node_id"] == route.node_ids[position + 1]
        assert assignment.eta_seconds >= 0
        assert assignment.predicted_response_seconds >= assignment.eta_seconds

    stale_approval = client.post(
        f"/dispatch/proposals/{original['proposal_id']}/approve",
        json={"dispatcher_id": "dispatcher-1"},
    )
    assert stale_approval.status_code == 409
    approved = _state(
        client.post(
            f"/dispatch/proposals/{updated['proposal_id']}/approve",
            json={"dispatcher_id": "dispatcher-1"},
        )
    )
    assert approved["approved"]["proposal_id"] == updated["proposal_id"]
    assert approved["approved"]["plan"] == updated["plan"]

    unblocked = _state(
        client.post(f"/roads/{edge_to_block}/unblock", json={"actor_id": "dispatcher-1"})
    )
    assert unblocked["graph_revision"] == blocked_state["graph_revision"] + 1
    assert next(
        road["status"] for road in unblocked["roads"] if road["edge_id"] == edge_to_block
    ) == "OPEN"


def test_planning_step_and_reset_do_not_claim_team_movement(client: TestClient) -> None:
    submitted = _submit(client, "One person is trapped at North.", "north")
    sos_id = submitted["reports"][0]["sos_id"]
    reviewed = _review(client, sos_id, "north", 4, ["basic"])
    before = _plan(client)
    before_teams = reviewed["teams"]

    stepped = _state(client.post("/simulation/step"))
    assert stepped["simulation_step"] == reviewed["simulation_step"] + 1
    assert stepped["graph_revision"] == reviewed["graph_revision"]
    assert stepped["teams"] == before_teams
    assert stepped["proposal"]["proposal_id"] != before["proposal_id"]
    assert stepped["proposal"]["plan"]["assignments"]
    assert [
        assignment["route"]["edge_ids"]
        for assignment in stepped["proposal"]["plan"]["assignments"]
    ] == [
        assignment["route"]["edge_ids"]
        for assignment in before["plan"]["assignments"]
    ]

    reset = _state(client.post("/simulation/reset"))
    assert reset["simulation_step"] == 0
    assert reset["reports"] == []
    assert reset["proposal"] is None
    assert reset["teams"] == before_teams
    assert client.get("/sos").json() == []


def test_invalid_inputs_have_clean_status_and_do_not_mutate_state(client: TestClient) -> None:
    initial = _state(client.get("/system/state"))
    assert client.post("/dispatch/plan").status_code == 409
    assert client.post("/sos", json={"text": 12}).status_code == 422
    assert client.post(
        "/sos", json={"text": "Help", "suggested_node_id": "not-a-node"}
    ).status_code == 400
    assert client.post(
        "/roads/unknown-edge/block", json={"actor_id": "dispatcher-1"}
    ).status_code == 404
    assert client.post(
        "/sos/unknown-report/review",
        json={
            "incident_node_id": "north",
            "dispatch_weight": 3,
            "required_capabilities": ["basic"],
            "reviewer_id": "reviewer-1",
        },
    ).status_code == 404
    assert _state(client.get("/system/state")) == initial

    edge = initial["roads"][0]["edge_id"]
    _state(client.post(f"/roads/{edge}/block", json={"actor_id": "dispatcher-1"}))
    conflict = client.post(f"/roads/{edge}/block", json={"actor_id": "dispatcher-1"})
    assert conflict.status_code == 409
    assert "detail" in conflict.json()
