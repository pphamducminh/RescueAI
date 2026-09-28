"""Minimal FastAPI application."""

from fastapi import FastAPI

from backend.api.schemas import HealthResponse

app = FastAPI(title="RescueAI", version="0.1.0")


@app.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    """Report that the API process is running."""
    return HealthResponse()
