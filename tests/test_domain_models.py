"""Validation at the shared domain boundary."""

import pytest
from pydantic import ValidationError

from backend.domain.models import RescueTeam


def test_capacity_requires_an_explicit_definition() -> None:
    with pytest.raises(ValidationError, match="capacity_definition"):
        RescueTeam(team_id="team-1", graph_node_id="node-1", capacity=4)

    team = RescueTeam(
        team_id="team-1",
        graph_node_id="node-1",
        capacity=4,
        capacity_definition="available passenger seats",
    )
    assert team.capacity == 4
