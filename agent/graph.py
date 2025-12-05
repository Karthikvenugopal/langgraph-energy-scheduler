"""LangGraph wiring for the EnergyScheduler agent.

Builds the linear graph

    START -> fetch_data -> classify_events -> analyze_fit -> restructure
          -> format_output -> END

and exposes :func:`run_agent` as the single entry point the API calls.
"""

from __future__ import annotations

from datetime import date as date_cls
from typing import Any, Optional

from langgraph.graph import END, START, StateGraph

from agent.nodes import (
    analyze_fit_node,
    classify_events_node,
    fetch_data_node,
    format_output_node,
    restructure_node,
)
from agent.state import EnergySchedulerState


def build_graph() -> Any:
    """Construct and compile the EnergyScheduler state graph.

    Returns:
        The compiled LangGraph runnable.
    """
    builder = StateGraph(EnergySchedulerState)

    builder.add_node("fetch_data", fetch_data_node)
    builder.add_node("classify_events", classify_events_node)
    builder.add_node("analyze_fit", analyze_fit_node)
    builder.add_node("restructure", restructure_node)
    builder.add_node("format_output", format_output_node)

    builder.add_edge(START, "fetch_data")
    builder.add_edge("fetch_data", "classify_events")
    builder.add_edge("classify_events", "analyze_fit")
    builder.add_edge("analyze_fit", "restructure")
    builder.add_edge("restructure", "format_output")
    builder.add_edge("format_output", END)

    return builder.compile()


# Compile once at import time and reuse across requests.
graph = build_graph()


def run_agent(date: Optional[str | date_cls] = None) -> dict[str, Any]:
    """Run the full optimization pipeline for a given day.

    Args:
        date: ``None`` for today, or an ISO ``YYYY-MM-DD`` string / ``date`` object.

    Returns:
        The final agent state, including ``events`` (original), ``classified_events``,
        ``energy_profile``, ``readiness_score``, ``data_source``,
        ``restructured_schedule``, ``fit_analysis``, and ``reasoning``.
    """
    target_date = date.isoformat() if isinstance(date, date_cls) else date
    initial_state: dict[str, Any] = {"target_date": target_date, "messages": []}
    return graph.invoke(initial_state)
