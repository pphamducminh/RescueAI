"""Environment-backed application configuration contracts."""

import pytest

from backend.config import Settings


def test_settings_read_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment and simulation seed can be configured without code edits."""
    monkeypatch.setenv("RESCUEAI_ENV", "test")
    monkeypatch.setenv("RESCUEAI_SIMULATION_SEED", "123")

    settings = Settings()

    assert settings.environment == "test"
    assert settings.simulation_seed == 123
