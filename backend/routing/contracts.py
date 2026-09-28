"""Dispatch-facing road-event and feasible-route interface."""

from typing import Protocol

from backend.domain.models import RoadEvent, Route


class RoadNetwork(Protocol):
    """A route returns None when the destination is unreachable."""

    def apply_event(self, event: RoadEvent) -> None: ...

    def find_route(self, origin_node_id: str, destination_node_id: str) -> Route | None: ...
