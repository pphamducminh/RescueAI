"""Dispatch strategy contract and legacy proposal interface."""

from collections.abc import Mapping, Sequence
from typing import Protocol

from backend.domain.models import AssignmentProposal, RescueTeam
from backend.domain.sos import PriorityAssessment, ValidatedSOS
from backend.routing.contracts import RoadNetwork

from .models import DispatchPlan, DispatchState


class DispatchStrategy(Protocol):
    """Solve one immutable dispatch snapshot without changing operational state."""

    @property
    def name(self) -> str: ...

    def solve(self, state: DispatchState) -> DispatchPlan: ...


class DispatchPolicy(Protocol):
    """Older SOS-snapshot adapter contract; strategies use DispatchState."""

    def propose(
        self,
        reports: Sequence[ValidatedSOS],
        teams: Sequence[RescueTeam],
        priorities: Mapping[str, PriorityAssessment],
        road_network: RoadNetwork,
        scenario_state_id: str,
    ) -> AssignmentProposal: ...
