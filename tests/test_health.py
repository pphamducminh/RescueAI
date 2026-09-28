"""Minimal API availability contract."""

from fastapi.testclient import TestClient

from backend.api.app import app


def test_health() -> None:
    """The service exposes an unauthenticated liveness endpoint."""
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
