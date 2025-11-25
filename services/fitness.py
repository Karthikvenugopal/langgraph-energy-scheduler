"""Google Fit client.

To get real data here, open Google Fit on Android or connect any Google Fit
compatible device. iPhone users will see None returned -- the energy module will
fall back to synthetic scores automatically.

Every function here returns ``None`` (never raises) when the Fitness API has no
data for the requested window, which is the normal case for users without a
connected device. That lets :mod:`services.energy` cleanly switch to its synthetic
curve.

Note: Google has announced the deprecation of the Google Fit REST APIs. The
implementation below follows the spec and remains useful for existing Fit data;
see the README for migration notes to alternatives.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Optional

from services.google_auth import get_google_service

# Aggregated data-source IDs exposed by Google Fit.
_STEPS_SOURCE = "derived:com.google.step_count.delta:com.google.android.gms:estimated_steps"
_ACTIVE_MINUTES_SOURCE = "derived:com.google.active_minutes:com.google.android.gms:merge_active_minutes"
_CALORIES_SOURCE = "derived:com.google.calories.expended:com.google.android.gms:merge_calories_expended"
_DISTANCE_SOURCE = "derived:com.google.distance.delta:com.google.android.gms:merge_distance_delta"
_SLEEP_SOURCE = "derived:com.google.sleep.segment:com.google.android.gms:merged"
_HEART_RATE_SOURCE = "derived:com.google.heart_rate.bpm:com.google.android.gms:merge_heart_rate_bpm"

# Google Fit sleep segment stage codes.
_SLEEP_LIGHT = 4
_SLEEP_DEEP = 5
_SLEEP_REM = 6
_ASLEEP_STAGES = {_SLEEP_LIGHT, _SLEEP_DEEP, _SLEEP_REM}


def _millis(dt: datetime) -> int:
    """Return milliseconds since the Unix epoch for ``dt``."""
    return int(dt.timestamp() * 1000)


def _nanos(dt: datetime) -> int:
    """Return nanoseconds since the Unix epoch for ``dt``."""
    return int(dt.timestamp() * 1_000_000_000)


def _yesterday_window() -> tuple[datetime, datetime]:
    """Return the 24-hour window ending at midnight today (i.e. all of yesterday)."""
    midnight_today = datetime.combine(datetime.now().date(), time.min)
    return midnight_today - timedelta(days=1), midnight_today


def _aggregate(source_id: str, start: datetime, end: datetime) -> Optional[list[dict[str, Any]]]:
    """Run a Fitness ``dataset:aggregate`` query for a single data source.

    Args:
        source_id: A ``derived:...`` data-source identifier.
        start: Window start.
        end: Window end.

    Returns:
        The list of ``bucket`` objects from the response, or ``None`` if the user is
        not authenticated or the request fails.
    """
    service = get_google_service("fitness", "v1")
    if service is None:
        return None

    body = {
        "aggregateBy": [{"dataSourceId": source_id}],
        "bucketByTime": {"durationMillis": _millis(end) - _millis(start)},
        "startTimeMillis": _millis(start),
        "endTimeMillis": _millis(end),
    }
    try:
        response = (
            service.users()
            .dataset()
            .aggregate(userId="me", body=body)
            .execute()
        )
    except Exception:  # noqa: BLE001 - no device / network / scope issues -> no data
        return None
    return response.get("bucket", [])


def _sum_int_values(buckets: Optional[list[dict[str, Any]]]) -> Optional[int]:
    """Sum integer point values across aggregation buckets.

    Returns:
        The integer total, or ``None`` if there were no data points at all (so the
        caller can distinguish "no device" from a genuine zero).
    """
    if not buckets:
        return None
    total = 0
    found = False
    for bucket in buckets:
        for dataset in bucket.get("dataset", []):
            for point in dataset.get("point", []):
                for value in point.get("value", []):
                    if "intVal" in value:
                        total += int(value["intVal"])
                        found = True
    return total if found else None


def _sum_float_values(buckets: Optional[list[dict[str, Any]]]) -> Optional[float]:
    """Sum floating-point point values across aggregation buckets.

    Returns:
        The float total, or ``None`` if there were no data points.
    """
    if not buckets:
        return None
    total = 0.0
    found = False
    for bucket in buckets:
        for dataset in bucket.get("dataset", []):
            for point in dataset.get("point", []):
                for value in point.get("value", []):
                    if "fpVal" in value:
                        total += float(value["fpVal"])
                        found = True
    return total if found else None


def get_activity_yesterday() -> Optional[dict[str, Any]]:
    """Return yesterday's activity totals from Google Fit.

    Returns:
        A dict ``{steps, active_minutes, calories_burned, distance_meters}`` for the
        24-hour window ending at midnight today, or ``None`` if no data is available
        (no connected device / not authenticated).
    """
    start, end = _yesterday_window()

    steps = _sum_int_values(_aggregate(_STEPS_SOURCE, start, end))
    active_minutes = _sum_int_values(_aggregate(_ACTIVE_MINUTES_SOURCE, start, end))
    calories = _sum_float_values(_aggregate(_CALORIES_SOURCE, start, end))
    distance = _sum_float_values(_aggregate(_DISTANCE_SOURCE, start, end))

    if steps is None and active_minutes is None and calories is None and distance is None:
        return None

    return {
        "steps": steps,
        "active_minutes": active_minutes,
        "calories_burned": round(calories, 1) if calories is not None else None,
        "distance_meters": round(distance, 1) if distance is not None else None,
    }


def get_sleep_last_night() -> Optional[dict[str, Any]]:
    """Return last night's sleep totals from Google Fit.

    Queries the sleep-segment data source over the window ending at midday today
    (so an early-morning wake-up is still captured) and tallies minutes by stage.

    Returns:
        A dict ``{total_sleep_minutes, deep_sleep_minutes, light_sleep_minutes}`` or
        ``None`` if no sleep data is available.
    """
    service = get_google_service("fitness", "v1")
    if service is None:
        return None

    end = datetime.combine(datetime.now().date(), time(12, 0))
    start = end - timedelta(days=1)
    dataset_id = f"{_nanos(start)}-{_nanos(end)}"

    try:
        response = (
            service.users()
            .dataSources()
            .datasets()
            .get(userId="me", dataSourceId=_SLEEP_SOURCE, datasetId=dataset_id)
            .execute()
        )
    except Exception:  # noqa: BLE001 - no data -> fall back
        return None

    points = response.get("point", [])
    if not points:
        return None

    total = deep = light = 0
    for point in points:
        stage = int(point["value"][0]["intVal"])
        minutes = (int(point["endTimeNanos"]) - int(point["startTimeNanos"])) / 6e10
        if stage in _ASLEEP_STAGES:
            total += minutes
            if stage == _SLEEP_DEEP:
                deep += minutes
            elif stage == _SLEEP_LIGHT:
                light += minutes

    if total == 0:
        return None

    return {
        "total_sleep_minutes": round(total),
        "deep_sleep_minutes": round(deep),
        "light_sleep_minutes": round(light),
    }


def get_resting_heart_rate() -> Optional[float]:
    """Return last night's resting heart rate (bpm) from Google Fit.

    Resting HR is approximated as the minimum recorded bpm over the overnight window
    — a common and robust proxy when an explicit resting-HR stream isn't present.

    Returns:
        The resting heart rate as a float, or ``None`` if no heart-rate data exists.
    """
    service = get_google_service("fitness", "v1")
    if service is None:
        return None

    end = datetime.combine(datetime.now().date(), time(12, 0))
    start = end - timedelta(days=1)
    dataset_id = f"{_nanos(start)}-{_nanos(end)}"

    try:
        response = (
            service.users()
            .dataSources()
            .datasets()
            .get(userId="me", dataSourceId=_HEART_RATE_SOURCE, datasetId=dataset_id)
            .execute()
        )
    except Exception:  # noqa: BLE001 - no data -> fall back
        return None

    bpms = [
        float(point["value"][0]["fpVal"])
        for point in response.get("point", [])
        if point.get("value")
    ]
    if not bpms:
        return None
    return round(min(bpms), 1)


def get_fitness_summary() -> dict[str, Any]:
    """Combine all three Fit reads into one dict for the energy engine and API.

    Returns:
        A dict with ``activity``, ``sleep`` and ``resting_heart_rate`` keys; each is
        the corresponding function's result (possibly ``None``). The energy engine
        treats an all-``None`` summary as "no data" and uses the synthetic curve.
    """
    return {
        "activity": get_activity_yesterday(),
        "sleep": get_sleep_last_night(),
        "resting_heart_rate": get_resting_heart_rate(),
    }
