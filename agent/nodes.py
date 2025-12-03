"""LangGraph nodes for the EnergyScheduler agent.

The graph runs five nodes in a fixed line:

    fetch_data -> classify_events -> analyze_fit -> restructure -> format_output

Every node prints a ``[node] ...`` line to stdout so the reasoning chain is easy to
follow while developing. The LLM-powered nodes (classification, restructuring) have
deterministic fallbacks, so the agent runs end-to-end with no Groq API key.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any, Optional

from agent.state import EnergySchedulerState
from agent.tools import (
    classify_event_type,
    find_best_slot,
    get_llm,
    score_event_fit,
)
from services import calendar as calendar_service
from services import energy as energy_service
from services import fitness as fitness_service

# Event types the agent never relocates: MEETINGs involve other people, while
# BREAK and PERSONAL blocks (lunch, appointments) are time-sensitive by nature.
_FIXED_TYPES = {"MEETING", "BREAK", "PERSONAL"}
# Freely movable solo work, ordered so DEEP_WORK claims peak-energy windows first
# and ADMIN settles into the leftover dips.
_MOVE_PRIORITY = ["DEEP_WORK", "ADMIN"]


def _log(node: str, message: str) -> None:
    """Print a namespaced transition line to stdout."""
    print(f"[{node}] {message}", flush=True)


def _fmt_time(iso: str) -> str:
    """Format an ISO timestamp as ``HH:MM`` for human-readable output."""
    return datetime.fromisoformat(iso).strftime("%H:%M")


# --------------------------------------------------------------------------- #
# Node 1: fetch data
# --------------------------------------------------------------------------- #
def fetch_data_node(state: EnergySchedulerState) -> dict[str, Any]:
    """Load calendar events, Google Fit data, and the energy profile into state.

    Args:
        state: Current agent state (reads optional ``target_date``).

    Returns:
        State updates: ``events``, ``fitness_data``, ``energy_profile``,
        ``readiness_score``, ``data_source``.
    """
    target_date = state.get("target_date")  # type: ignore[arg-type]
    _log("fetch_data", f"loading events for {target_date or 'today'}")

    events = calendar_service.get_events_today(target_date)
    fitness_data = fitness_service.get_fitness_summary()
    energy = energy_service.get_energy_profile(fitness_data)

    _log(
        "fetch_data",
        f"{len(events)} events | data_source={energy['data_source']} "
        f"| readiness={energy['readiness_score']}",
    )
    return {
        "events": events,
        "fitness_data": fitness_data,
        "energy_profile": energy["profile"],
        "readiness_score": energy["readiness_score"],
        "data_source": energy["data_source"],
    }


# --------------------------------------------------------------------------- #
# Node 2: classify events
# --------------------------------------------------------------------------- #
def classify_events_node(state: EnergySchedulerState) -> dict[str, Any]:
    """Classify each event into DEEP_WORK / MEETING / ADMIN / BREAK / PERSONAL.

    Args:
        state: Current agent state (reads ``events``).

    Returns:
        State update: ``classified_events`` (events with ``event_type`` set).
    """
    events = state.get("events", [])
    _log("classify_events", f"classifying {len(events)} events")

    classified: list[dict[str, Any]] = []
    for event in events:
        event_type = classify_event_type(event["title"], event.get("description", ""))
        labeled = {**event, "event_type": event_type}
        classified.append(labeled)
        _log("classify_events", f"{event['title']!r} -> {event_type}")

    return {"classified_events": classified}


# --------------------------------------------------------------------------- #
# Node 3: analyze fit
# --------------------------------------------------------------------------- #
def analyze_fit_node(state: EnergySchedulerState) -> dict[str, Any]:
    """Score each event against the energy profile and flag mismatches.

    Args:
        state: Current agent state (reads ``classified_events``, ``energy_profile``).

    Returns:
        State update: ``fit_analysis`` with per-event scores, mismatch flags, and a
        one-line summary.
    """
    events = state.get("classified_events", [])
    energy_profile = state.get("energy_profile", {})
    _log("analyze_fit", f"scoring {len(events)} events against energy profile")

    scored: list[dict[str, Any]] = []
    mismatches: list[str] = []
    for event in events:
        fit_score, mismatch = score_event_fit(event, energy_profile)
        scored.append(
            {
                "title": event["title"],
                "event_type": event["event_type"],
                "start": event["start"],
                "fit_score": fit_score,
                "mismatch": mismatch,
            }
        )
        if mismatch:
            mismatches.append(event["title"])
            _log(
                "analyze_fit",
                f"MISMATCH: {event['title']!r} ({event['event_type']}) at "
                f"{_fmt_time(event['start'])} — energy {fit_score}",
            )

    summary = (
        f"{len(mismatches)} of {len(events)} events are poorly matched to your energy"
        if mismatches
        else "All events are reasonably matched to your energy profile"
    )
    _log("analyze_fit", summary)

    return {
        "fit_analysis": {
            "events": scored,
            "mismatches": mismatches,
            "summary": summary,
        }
    }


# --------------------------------------------------------------------------- #
# Node 4: restructure
# --------------------------------------------------------------------------- #
def _rule_based_restructure(
    events: list[dict[str, Any]], energy_profile: dict[int, int]
) -> list[dict[str, Any]]:
    """Deterministically rebuild the schedule around the energy profile.

    Meetings stay put (they involve other people); solo blocks are placed into the
    energy windows that best fit their type, highest-priority first.

    Returns:
        The restructured schedule: one dict per event with ``moved`` and ``reason``.
    """
    fixed = [e for e in events if e["event_type"] in _FIXED_TYPES]
    movable = [e for e in events if e["event_type"] not in _FIXED_TYPES]

    # Booked windows start with the fixed meetings.
    booked: list[tuple[str, str]] = [(e["start"], e["end"]) for e in fixed]
    result: list[dict[str, Any]] = []

    for event in fixed:
        if event["event_type"] == "MEETING":
            reason = "Kept in place — involves other people."
        else:
            reason = "Kept in place — time-sensitive block."
        result.append(
            {**event, "original_start": event["start"], "moved": False, "reason": reason}
        )

    movable.sort(key=lambda e: _MOVE_PRIORITY.index(e["event_type"])
                 if e["event_type"] in _MOVE_PRIORITY else len(_MOVE_PRIORITY))

    for event in movable:
        duration = event["duration_minutes"]
        new_start_iso = find_best_slot(
            event["event_type"], duration, energy_profile, booked
        )
        new_start = datetime.fromisoformat(new_start_iso)
        new_end = new_start + timedelta(minutes=duration)
        moved = _fmt_time(new_start_iso) != _fmt_time(event["start"])

        if moved:
            reason = (
                f"Moved to {new_start.strftime('%H:%M')} to match a "
                f"{'high' if event['event_type'] == 'DEEP_WORK' else 'low'}-energy "
                f"window better suited to {event['event_type'].replace('_', ' ').lower()}."
            )
        else:
            reason = "Already in a well-matched energy window."

        booked.append((new_start.isoformat(), new_end.isoformat()))
        result.append(
            {
                **event,
                "start": new_start.isoformat(),
                "end": new_end.isoformat(),
                "original_start": event["start"],
                "moved": moved,
                "reason": reason,
            }
        )

    result.sort(key=lambda e: e["start"])
    return result


def _extract_json(text: str) -> Optional[dict[str, Any]]:
    """Best-effort extraction of a JSON object from an LLM response."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _llm_restructure(
    events: list[dict[str, Any]],
    energy_profile: dict[int, int],
    fit_analysis: dict[str, Any],
) -> Optional[list[dict[str, Any]]]:
    """Ask Groq to produce a restructured schedule; validate before trusting it.

    Returns:
        A validated schedule list, or ``None`` if the LLM is unavailable or its
        output fails validation (caller then uses the rule-based path).
    """
    llm = get_llm(temperature=0.2)
    if llm is None:
        return None

    compact_profile = ", ".join(f"{h}:{s}" for h, s in sorted(energy_profile.items()))
    event_lines = "\n".join(
        f"- {e['title']} | type={e['event_type']} | "
        f"start={e['start']} | end={e['end']} | duration={e['duration_minutes']}min"
        for e in events
    )
    system = (
        "You are EnergyScheduler, an assistant that rearranges a person's day so "
        "demanding work lands in high-energy hours and low-effort tasks land in dips. "
        "Rules: Only move DEEP_WORK and ADMIN blocks. PRESERVE the original times of "
        "MEETING (they involve other people), BREAK, and PERSONAL events (they are "
        "time-sensitive). Send DEEP_WORK to the highest-energy free window and ADMIN to "
        "a low-energy window. Keep every event on the same date, between 06:00 and "
        "23:00, with the same duration, and never overlap two events. Explain every "
        "move in plain English."
    )
    user = (
        f"Hourly energy (hour:score):\n{compact_profile}\n\n"
        f"Events:\n{event_lines}\n\n"
        f"Analysis: {fit_analysis.get('summary', '')}. "
        f"Mismatched events: {fit_analysis.get('mismatches', [])}.\n\n"
        "Return ONLY JSON of the form:\n"
        '{"schedule": [{"title": str, "start": ISO8601, "end": ISO8601, '
        '"event_type": str, "moved": bool, "reason": str}]}'
    )

    try:
        response = llm.invoke([("system", system), ("human", user)])
        parsed = _extract_json(str(response.content))
    except Exception:  # noqa: BLE001 - rate limits / network -> fall back
        return None

    if not parsed or "schedule" not in parsed:
        return None

    by_title = {e["title"]: e for e in events}
    validated: list[dict[str, Any]] = []
    try:
        for item in parsed["schedule"]:
            original = by_title.get(item["title"])
            if original is None:
                return None  # hallucinated event -> reject whole response
            start = datetime.fromisoformat(item["start"])
            end = datetime.fromisoformat(item["end"])
            if end <= start or not (6 <= start.hour <= 23):
                return None
            # Never trust the LLM to move a meeting.
            if original["event_type"] in _FIXED_TYPES:
                start = datetime.fromisoformat(original["start"])
                end = datetime.fromisoformat(original["end"])
            validated.append(
                {
                    **original,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "original_start": original["start"],
                    "moved": _fmt_time(start.isoformat()) != _fmt_time(original["start"]),
                    "reason": str(item.get("reason", "")).strip()
                    or "Adjusted to better match your energy.",
                }
            )
    except (KeyError, ValueError, TypeError):
        return None

    if len(validated) != len(events):
        return None
    validated.sort(key=lambda e: e["start"])
    return validated


def restructure_node(state: EnergySchedulerState) -> dict[str, Any]:
    """Produce a restructured schedule (LLM-driven, with a rule-based fallback).

    Args:
        state: Current agent state (reads ``classified_events``, ``energy_profile``,
            ``fit_analysis``).

    Returns:
        State update: ``restructured_schedule``.
    """
    events = state.get("classified_events", [])
    energy_profile = state.get("energy_profile", {})
    fit_analysis = state.get("fit_analysis", {})

    schedule = _llm_restructure(events, energy_profile, fit_analysis)
    if schedule is not None:
        _log("restructure", "schedule produced by Groq LLM")
    else:
        _log("restructure", "Groq unavailable/invalid — using rule-based scheduler")
        schedule = _rule_based_restructure(events, energy_profile)

    moves = sum(1 for e in schedule if e.get("moved"))
    _log("restructure", f"{moves} event(s) moved")
    return {"restructured_schedule": schedule}


# --------------------------------------------------------------------------- #
# Node 5: format output
# --------------------------------------------------------------------------- #
def format_output_node(state: EnergySchedulerState) -> dict[str, Any]:
    """Build the final clean schedule and human-readable reasoning bullets.

    Args:
        state: Current agent state (reads ``restructured_schedule``).

    Returns:
        State update: ``restructured_schedule`` (cleaned) and ``reasoning`` bullets.
    """
    schedule = state.get("restructured_schedule", [])
    _log("format_output", f"formatting {len(schedule)} events")

    reasoning: list[str] = []
    clean_schedule: list[dict[str, Any]] = []
    for event in schedule:
        clean_schedule.append(
            {
                "title": event["title"],
                "start": event["start"],
                "end": event["end"],
                "duration_minutes": event["duration_minutes"],
                "event_type": event["event_type"],
                "original_start": event.get("original_start", event["start"]),
                "moved": event.get("moved", False),
            }
        )
        if event.get("moved"):
            reasoning.append(
                f"🔀 {event['title']}: {_fmt_time(event['original_start'])} → "
                f"{_fmt_time(event['start'])} — {event.get('reason', '')}"
            )
        else:
            reasoning.append(f"✅ {event['title']}: kept at {_fmt_time(event['start'])}")

    move_count = sum(1 for e in clean_schedule if e["moved"])
    header = (
        f"Rebalanced your day — moved {move_count} block(s) to better match your energy."
        if move_count
        else "Your day already matches your energy well — no changes needed."
    )
    reasoning.insert(0, header)
    _log("format_output", header)

    return {"restructured_schedule": clean_schedule, "reasoning": reasoning}
