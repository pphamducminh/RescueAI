"""Deterministic routing and graph-update behavior for the road graph."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from backend.domain.models import RoadEvent
from backend.routing import DynamicRoadGraph, Road, RoadStatus, RoutingCostConfig

UPDATED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def _road(
    edge_id: str,
    source: str,
    target: str,
    *,
    travel_time: float = 10,
    distance: float = 100,
    risk: float = 0,
    status: RoadStatus = RoadStatus.OPEN,
    updated_at: datetime = UPDATED_AT,
) -> Road:
    return Road(
        edge_id=edge_id,
        source_node_id=source,
        target_node_id=target,
        distance_meters=float(distance),
        base_travel_time_seconds=float(travel_time),
        risk_score=float(risk),
        status=status,
        last_updated=updated_at,
    )


def _graph(*node_ids: str, risk_penalty: float = 0) -> DynamicRoadGraph:
    graph = DynamicRoadGraph(
        RoutingCostConfig(
            risk_penalty_seconds_per_unit=float(risk_penalty),
            degraded_time_multiplier=2.0,
        )
    )
    for node_id in node_ids:
        graph.add_node(node_id)
    return graph


def test_simple_graph_chooses_least_cost_route_and_records_metrics() -> None:
    graph = _graph("A", "B", "C")
    graph.add_road(_road("direct", "A", "C", travel_time=20, distance=200))
    graph.add_road(_road("ab", "A", "B", travel_time=4, distance=40))
    graph.add_road(_road("bc", "B", "C", travel_time=5, distance=50))

    route = graph.shortest_safe_route("A", "C")

    assert route is not None
    assert route.node_ids == ("A", "B", "C")
    assert route.edge_ids == ("ab", "bc")
    assert route.travel_time_seconds == pytest.approx(9)
    assert route.distance_meters == pytest.approx(90)
    assert route.effective_cost_seconds == pytest.approx(9)
    assert route.risk_score_sum == pytest.approx(0)
    assert route.graph_revision == graph.get_state().revision


def test_disconnected_graph_and_unknown_nodes_have_no_feasible_path() -> None:
    graph = _graph("A", "B", "C")
    graph.add_road(_road("ab", "A", "B"))

    assert graph.shortest_safe_route("A", "C") is None
    assert graph.shortest_safe_route("C", "A") is None
    assert graph.shortest_safe_route("missing", "B") is None


def test_bridge_blocked_replans_without_entering_blocked_edge() -> None:
    graph = _graph("A", "B", "C", "D")
    graph.add_road(_road("ab", "A", "B", travel_time=2))
    graph.add_road(_road("bc", "B", "C", travel_time=2))
    graph.add_road(_road("ad", "A", "D", travel_time=5))
    graph.add_road(_road("dc", "D", "C", travel_time=5))
    original = graph.shortest_safe_route("A", "C")
    assert original is not None
    assert original.edge_ids == ("ab", "bc")

    blocked_at = UPDATED_AT + timedelta(seconds=1)
    graph.block_road("bc", updated_at=blocked_at)
    replanned = graph.recompute_route(original)

    assert graph.get_road("bc").status == RoadStatus.BLOCKED
    assert graph.get_road("bc").last_updated == blocked_at
    assert replanned is not None
    assert replanned.edge_ids == ("ad", "dc")
    assert "bc" not in replanned.edge_ids
    assert replanned.graph_revision > original.graph_revision

    graph.unblock_road("bc", updated_at=blocked_at + timedelta(seconds=1))
    restored = graph.shortest_safe_route("A", "C")
    assert restored is not None
    assert restored.edge_ids == ("ab", "bc")


def test_blocked_only_link_returns_no_route() -> None:
    graph = _graph("A", "B")
    graph.add_road(_road("ab", "A", "B", status=RoadStatus.BLOCKED))

    assert graph.shortest_safe_route("A", "B") is None


def test_configurable_risk_penalty_prefers_safer_longer_road() -> None:
    no_penalty = _graph("A", "B", "C", risk_penalty=0)
    risk_aware = _graph("A", "B", "C", risk_penalty=10)
    for graph in (no_penalty, risk_aware):
        graph.add_road(_road("risky", "A", "C", travel_time=5, risk=1))
        graph.add_road(_road("safe_ab", "A", "B", travel_time=4))
        graph.add_road(_road("safe_bc", "B", "C", travel_time=4))

    fastest = no_penalty.shortest_safe_route("A", "C")
    safest = risk_aware.shortest_safe_route("A", "C")

    assert fastest is not None and safest is not None
    assert fastest.edge_ids == ("risky",)
    assert fastest.effective_cost_seconds == pytest.approx(5)
    assert safest.edge_ids == ("safe_ab", "safe_bc")
    assert safest.effective_cost_seconds == pytest.approx(8)


def test_degraded_road_uses_configured_time_multiplier() -> None:
    graph = _graph("A", "B", "C")
    graph.add_road(_road("degraded", "A", "C", travel_time=5, status=RoadStatus.DEGRADED))
    graph.add_road(_road("ab", "A", "B", travel_time=4))
    graph.add_road(_road("bc", "B", "C", travel_time=4))

    route = graph.shortest_safe_route("A", "C")

    assert route is not None
    assert route.edge_ids == ("ab", "bc")
    assert route.effective_cost_seconds == pytest.approx(8)


def test_equal_cost_paths_choose_lexicographic_edge_ids_independent_of_insertion() -> None:
    roads = (
        _road("ac", "A", "C", travel_time=2),
        _road("cd", "C", "D", travel_time=2),
        _road("ab", "A", "B", travel_time=2),
        _road("bd", "B", "D", travel_time=2),
    )
    paths: list[tuple[str, ...]] = []
    for order in (roads, tuple(reversed(roads))):
        graph = _graph("A", "B", "C", "D")
        for road in order:
            graph.add_road(road)
        route = graph.shortest_safe_route("A", "D")
        assert route is not None
        paths.append(route.edge_ids)

    assert paths == [("ab", "bd"), ("ab", "bd")]


def test_repeated_queries_return_identical_routes() -> None:
    graph = _graph("A", "B", "C")
    graph.add_road(_road("ab", "A", "B", travel_time=2))
    graph.add_road(_road("bc", "B", "C", travel_time=3))

    routes = [graph.shortest_safe_route("A", "C") for _ in range(5)]

    assert routes[0] is not None
    assert routes == [routes[0]] * 5
    same_node = graph.shortest_safe_route("A", "A")
    assert same_node is not None
    assert same_node.node_ids == ("A",)
    assert same_node.edge_ids == ()
    assert same_node.effective_cost_seconds == 0


def test_changing_risk_penalty_recomputes_preferred_route() -> None:
    graph = _graph("A", "B", "C")
    graph.add_road(_road("direct", "A", "C", travel_time=5, risk=1))
    graph.add_road(_road("ab", "A", "B", travel_time=4))
    graph.add_road(_road("bc", "B", "C", travel_time=4))
    original = graph.shortest_safe_route("A", "C")
    assert original is not None
    assert original.edge_ids == ("direct",)

    graph.set_cost_config(
        RoutingCostConfig(risk_penalty_seconds_per_unit=10.0, degraded_time_multiplier=2.0)
    )
    replanned = graph.recompute_route(original)

    assert replanned is not None
    assert replanned.edge_ids == ("ab", "bc")
    assert replanned.graph_revision > original.graph_revision


def test_update_remove_and_query_graph_state() -> None:
    graph = _graph("A", "B", "C")
    graph.add_road(_road("ab", "A", "B"))
    before = graph.get_state()

    replacement = _road(
        "ab", "A", "C", travel_time=3, risk=0.25, updated_at=UPDATED_AT + timedelta(seconds=1)
    )
    graph.update_road(replacement)
    after = graph.get_state()

    assert graph.get_road("ab") == replacement
    assert after.revision > before.revision
    assert after.node_ids == ("A", "B", "C")
    assert after.roads == (replacement,)
    assert graph.shortest_safe_route("A", "B") is None
    assert graph.shortest_safe_route("A", "C") is not None

    graph.remove_road("ab")
    assert graph.get_state().roads == ()
    assert graph.shortest_safe_route("A", "C") is None


def test_risk_update_changes_route_and_retains_timestamp() -> None:
    graph = _graph("A", "B", "C", risk_penalty=10)
    graph.add_road(_road("direct", "A", "C", travel_time=5, risk=0))
    graph.add_road(_road("ab", "A", "B", travel_time=4))
    graph.add_road(_road("bc", "B", "C", travel_time=4))
    original = graph.shortest_safe_route("A", "C")
    assert original is not None
    assert original.edge_ids == ("direct",)

    updated_at = UPDATED_AT + timedelta(seconds=1)
    graph.update_risk("direct", risk_score=1, updated_at=updated_at)
    replanned = graph.recompute_route(original)

    assert graph.get_road("direct").risk_score == 1
    assert graph.get_road("direct").last_updated == updated_at
    assert replanned is not None
    assert replanned.edge_ids == ("ab", "bc")


def test_recompute_route_can_start_from_current_node() -> None:
    graph = _graph("A", "B", "C", "D")
    graph.add_road(_road("ab", "A", "B"))
    graph.add_road(_road("bc", "B", "C"))
    graph.add_road(_road("bd", "B", "D"))
    graph.add_road(_road("dc", "D", "C"))
    original = graph.shortest_safe_route("A", "C")
    assert original is not None

    graph.block_road("bc", updated_at=UPDATED_AT + timedelta(seconds=1))
    replanned = graph.recompute_route(original, current_node_id="B")

    assert replanned is not None
    assert replanned.node_ids == ("B", "D", "C")
    assert replanned.edge_ids == ("bd", "dc")


def test_legacy_road_network_protocol_methods_use_same_graph() -> None:
    graph = _graph("A", "B")
    graph.add_road(_road("ab", "A", "B"))

    route = graph.find_route("A", "B")
    assert route is not None
    assert route.node_ids == ["A", "B"]
    assert route.edge_ids == ["ab"]

    graph.apply_event(
        RoadEvent(edge_id="ab", occurred_at=UPDATED_AT + timedelta(seconds=1), blocked=True)
    )
    assert graph.find_route("A", "B") is None


def test_invalid_road_and_cost_configuration_are_rejected() -> None:
    with pytest.raises(ValidationError):
        _road("bad", "A", "B", risk=-0.1)
    with pytest.raises(ValidationError):
        _road("bad", "A", "B", travel_time=0)
    with pytest.raises(ValidationError):
        _road("bad", "A", "B", updated_at=datetime(2026, 1, 1))
    with pytest.raises(ValidationError):
        RoutingCostConfig(risk_penalty_seconds_per_unit=-1.0, degraded_time_multiplier=2.0)


def test_road_updates_reject_stale_events() -> None:
    graph = _graph("A", "B")
    graph.add_road(_road("ab", "A", "B", updated_at=UPDATED_AT + timedelta(seconds=1)))

    with pytest.raises(ValueError):
        graph.block_road("ab", updated_at=UPDATED_AT)
    assert graph.get_road("ab").status == RoadStatus.OPEN
