"""LangGraph state definition for the EnergyScheduler agent.

The state is a plain :class:`TypedDict` that flows through every node. Each node
reads the fields it needs and writes the fields it produces, so the full reasoning
chain — raw events, classifications, fit analysis, and the final restructured
schedule — is inspectable at any point.
"""

from __future__ import annotations

from typing import Annotated, Any, Optional, TypedDict

from langgraph.graph.message import add_messages


class EnergySchedulerState(TypedDict, total=False):
    """Shared state passed between LangGraph nodes.

    Attributes:
        target_date: ISO ``YYYY-MM-DD`` date to optimize (``None`` = today).
        events: Raw calendar events for the target day.
        energy_profile: Hourly energy scores ``{hour (6–23) -> score 0–100}``.
        readiness_score: Overall 0–100 day readiness score.
        data_source: ``"google_fit"`` or ``"synthetic"``.
        fitness_data: Combined Fit summary ``{activity, sleep, resting_heart_rate}``.
        classified_events: Events annotated with an ``event_type`` label.
        fit_analysis: Per-event fit scores, mismatch flags, and a summary string.
        restructured_schedule: The agent's proposed new schedule.
        reasoning: Human-readable bullet points explaining each change.
        messages: LLM message history (accumulated via the ``add_messages`` reducer).
    """

    target_date: Optional[str]
    events: list[dict[str, Any]]
    energy_profile: dict[int, int]
    readiness_score: int
    data_source: str
    fitness_data: Optional[dict[str, Any]]
    classified_events: list[dict[str, Any]]
    fit_analysis: dict[str, Any]
    restructured_schedule: list[dict[str, Any]]
    reasoning: list[str]
    messages: Annotated[list, add_messages]


# The target date is passed in as part of the initial state under this key.
TARGET_DATE_KEY = "target_date"
