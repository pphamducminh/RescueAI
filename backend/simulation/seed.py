"""Independent, reproducible random generators for future simulations."""

from random import Random

from backend.config import Settings


def make_rng(seed: int | None = None) -> Random:
    """Create a fresh generator from an explicit or configured seed."""
    return Random(Settings().simulation_seed if seed is None else seed)
