"""Deterministic simulation randomness contract."""

from random import Random

import pytest

from backend.simulation.seed import make_rng


def test_make_rng_replays_identical_sequence() -> None:
    """Separate generators with the same seed replay the same draws."""
    first = make_rng(123)
    second = make_rng(123)

    assert isinstance(first, Random)
    assert isinstance(second, Random)
    assert first is not second
    assert [first.random() for _ in range(5)] == [second.random() for _ in range(5)]


def test_make_rng_uses_configured_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESCUEAI_SIMULATION_SEED", "42")

    assert make_rng().random() == make_rng(42).random()
