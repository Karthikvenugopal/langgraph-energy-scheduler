"""Agent tools: event classification, fit scoring, and optimal-slot search.

These are plain, well-typed functions the LangGraph nodes call. The LLM (Groq) is
used only where judgement helps — classifying an event's type — and every LLM path
has a deterministic keyword fallback so the agent runs with **no API key at all**.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Optional

# Valid event categories. DEEP_WORK and important MEETINGs want high-energy slots;
# ADMIN/BREAK/PERSONAL can live in low-energy windows.
EVENT_TYPES = ["DEEP_WORK", "MEETING", "ADMIN", "BREAK", "PERSONAL"]

DEFAULT_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# Keyword fallbacks, checked in priority order (first match wins).
_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("MEETING", ("standup", "stand-up", "sync", "meeting", "review", "1:1", "1-1",
                 "call", "interview", "demo", "retro", "planning meeting", "catch up")),
    ("BREAK", ("lunch", "break", "coffee", "gym", "workout", "walk", "rest")),
    ("DEEP_WORK", ("deep work", "focus", "writing", "write", "coding", "code", "build",
                   "research", "draft", "design", "architecture", "prototype")),
    ("ADMIN", ("email", "admin", "expenses", "expense", "invoice", "triage", "inbox",
               "paperwork", "planning", "timesheet", "errand")),
    ("PERSONAL", ("personal", "doctor", "appointment", "family", "dentist", "school")),
]


def get_llm(temperature: float = 0.3) -> Optional[Any]:
    """Return a configured Groq chat model, or ``None`` if no API key is set.

    Args:
        temperature: Sampling temperature for the model.

    Returns:
        A ``ChatGroq`` instance, or ``None`` when ``GROQ_API_KEY`` is absent (the
        caller then uses deterministic fallbacks).
    """
    if not os.getenv("GROQ_API_KEY"):
        return None
    try:
        from langchain_groq import ChatGroq
    except ImportError:
        return None
    return ChatGroq(model=DEFAULT_MODEL, temperature=temperature)


def _classify_by_keyword(title: str, description: str) -> str:
    """Classify an event using keyword matching (deterministic fallback)."""
    haystack = f"{title} {description}".lower()
    for label, keywords in _KEYWORDS:
        if any(keyword in haystack for keyword in keywords):
            return label
    return "ADMIN"  # safe default: low-energy, freely movable


def classify_event_type(title: str, description: str = "") -> str:
    """Classify a calendar event into one of :data:`EVENT_TYPES`.

    Uses the Groq LLM when available, validating its answer against the allowed
    labels; otherwise (or on any failure) falls back to keyword matching.

    Args:
        title: The event title.
        description: The event description (optional).

    Returns:
        One of ``DEEP_WORK``, ``MEETING``, ``ADMIN``, ``BREAK``, ``PERSONAL``.
    """
    llm = get_llm(temperature=0.0)
    if llm is not None:
        prompt = (
            "Classify this calendar event into exactly one category: "
            f"{', '.join(EVENT_TYPES)}.\n"
            "Guidance: DEEP_WORK = solo cognitively demanding focus work; "
            "MEETING = involves other people; ADMIN = low-effort tasks like email/"
            "expenses/triage; BREAK = lunch/rest/exercise; PERSONAL = personal "
            "appointments.\n"
            f"Title: {title}\nDescription: {description}\n"
            "Respond with ONLY the category word."
        )
        try:
            response = llm.invoke(prompt)
            answer = str(response.content).strip().upper()
            for label in EVENT_TYPES:
                if label in answer:
                    return label
        except Exception:  # noqa: BLE001 - rate limits / network -> fall back
            pass
    return _classify_by_keyword(title, description)


def _parse_dt(value: Any) -> datetime:
    """Coerce an ISO string or ``datetime`` into a naive ``datetime``."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    return datetime.fromisoformat(str(value)).replace(tzinfo=None)


def _window_energy(start: datetime, end: datetime, energy_profile: dict[int, int]) -> float:
    """Return the average energy across the hours a ``[start, end)`` window touches.

    Hours outside the 06:00–23:00 profile are treated as low energy (20).
    """
    if end <= start:
        return float(energy_profile.get(start.hour, 20))
    hours: list[int] = []
    cursor = start
    while cursor < end:
        hours.append(cursor.hour)
        cursor += timedelta(minutes=30)
    scores = [energy_profile.get(hour, 20) for hour in hours]
    return sum(scores) / len(scores) if scores else 20.0


def score_event_fit(
    event: dict[str, Any], energy_profile: dict[int, int]
) -> tuple[int, bool]:
    """Score how well an event's slot matches the available energy.

    Args:
        event: An event dict with ``start``, ``end``, and ``event_type``.
        energy_profile: Hourly energy scores ``{hour -> score}``.

    Returns:
        ``(fit_score, mismatch)`` where ``fit_score`` (0–100) is the average energy
        in the event's slot, and ``mismatch`` is ``True`` when a DEEP_WORK event sits
        in energy below 60 or a MEETING sits in energy below 50.
    """
    start = _parse_dt(event["start"])
    end = _parse_dt(event["end"])
    slot_energy = _window_energy(start, end, energy_profile)
    fit_score = int(round(slot_energy))

    event_type = event.get("event_type")
    mismatch = (
        (event_type == "DEEP_WORK" and slot_energy < 60)
        or (event_type == "MEETING" and slot_energy < 50)
    )
    return fit_score, mismatch


def _overlaps(start: datetime, end: datetime, booked: list[tuple[datetime, datetime]]) -> bool:
    """Return ``True`` if ``[start, end)`` overlaps any booked window."""
    return any(start < b_end and end > b_start for b_start, b_end in booked)


def _preference_score(event_type: str, slot_energy: float) -> float:
    """Rank a candidate slot's energy for a given event type (higher = better).

    DEEP_WORK prefers the highest energy, ADMIN/BREAK/PERSONAL the lowest, and
    MEETINGs a moderate band centered around 68.
    """
    if event_type == "DEEP_WORK":
        return slot_energy
    if event_type == "MEETING":
        return 100 - abs(slot_energy - 68)
    # ADMIN / BREAK / PERSONAL -> prefer low energy so peaks stay free for focus.
    return 100 - slot_energy


def find_best_slot(
    event_type: str,
    duration_minutes: int,
    energy_profile: dict[int, int],
    booked_slots: list[tuple[Any, Any]],
    day: Optional[datetime] = None,
) -> str:
    """Find the optimal free start time for an event given its type and energy.

    Searches 06:00–23:00 in 30-minute steps for a free window whose energy best
    matches the event type (DEEP_WORK → highest energy, MEETING → moderate,
    ADMIN/BREAK/PERSONAL → lowest), skipping windows that overlap ``booked_slots``.

    Args:
        event_type: One of :data:`EVENT_TYPES`.
        duration_minutes: Event length in minutes.
        energy_profile: Hourly energy scores ``{hour -> score}``.
        booked_slots: Already-occupied ``(start, end)`` windows (ISO strings or
            ``datetime`` objects).
        day: The day to schedule on; inferred from ``booked_slots`` or today.

    Returns:
        The chosen start time as an ISO 8601 string.
    """
    booked = [(_parse_dt(s), _parse_dt(e)) for s, e in booked_slots]

    if day is not None:
        base_day = day.replace(hour=0, minute=0, second=0, microsecond=0)
    elif booked:
        base_day = booked[0][0].replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        base_day = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    best_start: Optional[datetime] = None
    best_rank = float("-inf")

    minute = 6 * 60
    last_start = 23 * 60 - duration_minutes
    while minute <= last_start:
        start = base_day + timedelta(minutes=minute)
        end = start + timedelta(minutes=duration_minutes)
        if not _overlaps(start, end, booked):
            rank = _preference_score(event_type, _window_energy(start, end, energy_profile))
            if rank > best_rank:
                best_rank = rank
                best_start = start
        minute += 30

    # If nothing fit (day fully booked), keep the event at its current/anchored time.
    if best_start is None:
        best_start = booked[0][0] if booked else base_day + timedelta(hours=9)

    return best_start.isoformat()
