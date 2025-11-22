"""Google Calendar client.

Exposes two read helpers — :func:`get_events_today` and :func:`get_events_week` —
that return plain dicts the rest of the app can consume. When the user has not
connected Google (no ``token.json``) the module returns a realistic set of demo
events so the whole experience works with zero setup.
"""

from __future__ import annotations

from datetime import date as date_cls
from datetime import datetime, time, timedelta
from typing import Any, Optional

from services.google_auth import get_google_service, has_token

# Each demo event is (title, start hour, start minute, duration minutes, description).
_DEMO_EVENTS: list[tuple[str, int, int, int, str]] = [
    ("Daily Standup", 9, 0, 30, "Team sync — status updates with the squad."),
    ("Deep Work: Pricing model", 10, 0, 90, "Heads-down work on the pricing engine."),
    ("Lunch", 12, 0, 60, "Lunch break."),
    ("Design Review", 14, 0, 60, "Review new dashboard designs with design + PM."),
    ("Focus Block: Bug triage", 16, 0, 90, "Solo focus block to clear the bug backlog."),
    ("1:1 with Manager", 17, 0, 30, "Weekly 1:1."),
]


def _resolve_date(date: Optional[str | date_cls]) -> date_cls:
    """Normalize a date argument into a :class:`datetime.date`.

    Args:
        date: ``None`` (today), an ISO ``YYYY-MM-DD`` string, or a ``date`` object.

    Returns:
        The resolved :class:`datetime.date`.
    """
    if date is None:
        return date_cls.today()
    if isinstance(date, date_cls):
        return date
    return date_cls.fromisoformat(date)


def _demo_events_for(target: date_cls) -> list[dict[str, Any]]:
    """Build the hardcoded demo schedule anchored to ``target``.

    Args:
        target: The day the demo events should fall on.

    Returns:
        A list of event dicts identical in shape to live Calendar events.
    """
    events: list[dict[str, Any]] = []
    for title, hour, minute, duration, description in _DEMO_EVENTS:
        start_dt = datetime.combine(target, time(hour, minute))
        end_dt = start_dt + timedelta(minutes=duration)
        events.append(
            {
                "title": title,
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "duration_minutes": duration,
                "description": description,
                "event_type": None,
            }
        )
    return events


def _parse_api_event(raw: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Convert a raw Google Calendar API event into our normalized dict.

    Skips all-day events (which have ``date`` instead of ``dateTime``) since the
    scheduler operates on timed blocks.

    Args:
        raw: A single event resource from the Calendar API.

    Returns:
        A normalized event dict, or ``None`` for events we don't schedule.
    """
    start_info = raw.get("start", {})
    end_info = raw.get("end", {})
    start_raw = start_info.get("dateTime")
    end_raw = end_info.get("dateTime")
    if not start_raw or not end_raw:
        return None  # all-day or malformed event

    start_dt = datetime.fromisoformat(start_raw)
    end_dt = datetime.fromisoformat(end_raw)
    duration_minutes = max(0, int((end_dt - start_dt).total_seconds() // 60))

    return {
        "title": raw.get("summary", "(no title)"),
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "duration_minutes": duration_minutes,
        "description": raw.get("description", ""),
        "event_type": None,
    }


def _fetch_events(time_min: datetime, time_max: datetime) -> list[dict[str, Any]]:
    """Fetch and normalize timed events in ``[time_min, time_max)`` from Calendar.

    Args:
        time_min: Inclusive lower bound.
        time_max: Exclusive upper bound.

    Returns:
        Normalized event dicts. Returns demo events if not authenticated and an
        empty list if the API call fails for any reason.
    """
    service = get_google_service("calendar", "v3")
    if service is None:
        return _demo_events_for(time_min.date())

    try:
        response = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=time_min.astimezone().isoformat(),
                timeMax=time_max.astimezone().isoformat(),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
    except Exception:  # noqa: BLE001 - network/auth errors -> behave like no data
        return []

    parsed = [_parse_api_event(item) for item in response.get("items", [])]
    return [event for event in parsed if event is not None]


def get_events_today(date: Optional[str | date_cls] = None) -> list[dict[str, Any]]:
    """Return the day's timed events.

    Args:
        date: ``None`` for today, or an ISO date string / ``date`` object.

    Returns:
        A list of event dicts with ``title``, ``start``, ``end``,
        ``duration_minutes``, ``description``, and ``event_type`` (``None`` until
        the agent classifies it). Falls back to demo events when not authenticated.
    """
    target = _resolve_date(date)
    if not has_token():
        return _demo_events_for(target)

    start_of_day = datetime.combine(target, time.min)
    end_of_day = datetime.combine(target, time.max)
    return _fetch_events(start_of_day, end_of_day)


def get_calendar_name() -> Optional[str]:
    """Return the connected user's primary calendar name, if authenticated.

    Returns:
        The calendar's display name (its ``summary``, usually the user's email), or
        ``None`` when not connected or the lookup fails.
    """
    service = get_google_service("calendar", "v3")
    if service is None:
        return None
    try:
        primary = service.calendars().get(calendarId="primary").execute()
    except Exception:  # noqa: BLE001 - treat any failure as "unknown"
        return None
    return primary.get("summary")


def get_events_week() -> list[dict[str, Any]]:
    """Return timed events for the next seven days (starting today).

    Returns:
        A list of normalized event dicts across the upcoming week. Falls back to a
        week's worth of demo events when not authenticated.
    """
    today = date_cls.today()
    if not has_token():
        week: list[dict[str, Any]] = []
        for offset in range(7):
            week.extend(_demo_events_for(today + timedelta(days=offset)))
        return week

    start = datetime.combine(today, time.min)
    end = start + timedelta(days=7)
    return _fetch_events(start, end)
