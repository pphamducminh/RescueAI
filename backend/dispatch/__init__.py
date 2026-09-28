"""Deterministic one-wave dispatch proposals and strategy registry."""

from .contracts import DispatchStrategy
from .models import (
    DispatchAssignment,
    DispatchPlan,
    DispatchRequest,
    DispatchState,
    DispatchTeam,
    FeasiblePair,
    ObjectiveValue,
    PlanStatus,
    UnservedReason,
    UnservedRequest,
)
from .state import build_dispatch_state
from .strategies import (
    FCFS,
    NearestTeam,
    PriorityAwareGreedy,
    RescueAIOptimizer,
    get_strategy,
    list_strategies,
)

__all__ = [
    "DispatchAssignment",
    "DispatchPlan",
    "DispatchRequest",
    "DispatchState",
    "DispatchStrategy",
    "DispatchTeam",
    "FCFS",
    "FeasiblePair",
    "NearestTeam",
    "ObjectiveValue",
    "PlanStatus",
    "PriorityAwareGreedy",
    "RescueAIOptimizer",
    "UnservedReason",
    "UnservedRequest",
    "build_dispatch_state",
    "get_strategy",
    "list_strategies",
]
