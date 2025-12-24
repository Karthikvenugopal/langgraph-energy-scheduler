"""Whoop API client (Whoop Developer API v2).

Maps Whoop's recovery, sleep, and cycle data into the same normalized dict the
energy engine consumes (see :mod:`services.energy`) — plus Whoop's first-class
**recovery score** and **HRV**, which are richer recovery signals than the
resting-heart-rate proxy used for Google Fit.

To use Whoop instead of Google Fit, register an app at https://developer.whoop.com,
set ``WHOOP_CLIENT_ID`` / ``WHOOP_CLIENT_SECRET`` and ``WEARABLE_SOURCE=whoop``, and
connect via ``/auth/whoop``. Every function returns ``None`` on missing data/errors,
so the energy module cleanly falls back to another source or synthetic scores.
"""

from __future__ import annotations

from typing import Any, Optional

import requests

from services import whoop_auth

_MILLIS_PER_MIN = 60_000


def _get(path: str, params: dict[str, Any]) -> Optional[dict[str, Any]]:
    """GET a Whoop v2 endpoint with the current bearer token.

    Args:
        path: Path under the v2 base, e.g. ``"/recovery"``.
        params: Query parameters.

    Returns:
        The decoded JSON body, or ``None`` if not authenticated or the request fails.
    """
    token = whoop_auth.get_access_token()
    if token is None:
        return None
    try:
        resp = requests.get(
            f"{whoop_auth.API_BASE}{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException:
        return None
    return resp.json()


def _latest_record(path: str) -> Optional[dict[str, Any]]:
    """Return the most recent record from a paginated Whoop collection."""
    data = _get(path, {"limit": 1})
    if not data:
        return None
    records = data.get("records") or []
    return records[0] if records else None


def get_recovery_latest() -> Optional[dict[str, Any]]:
    """Return the latest scored recovery from ``GET /recovery``.

    Returns:
        ``{recovery_score, resting_heart_rate, hrv_rmssd_milli}`` (recovery_score is
        Whoop's 0–100 %), or ``None`` if unavailable or not yet scored.
    """
    record = _latest_record("/recovery")
    if not record or record.get("score_state") != "SCORED":
        return None
    score = record.get("score") or {}
    return {
        "recovery_score": score.get("recovery_score"),
        "resting_heart_rate": score.get("resting_heart_rate"),
        "hrv_rmssd_milli": score.get("hrv_rmssd_milli"),
    }


def get_sleep_last_night() -> Optional[dict[str, Any]]:
    """Return last night's sleep from ``GET /activity/sleep``.

    Sums Whoop's per-stage durations (light + slow-wave + REM) into total asleep
    minutes, and reports deep (slow-wave) and light minutes.

    Returns:
        ``{total_sleep_minutes, deep_sleep_minutes, light_sleep_minutes}`` or ``None``.
    """
    record = _latest_record("/activity/sleep")
    if not record:
        return None
    stages = (record.get("score") or {}).get("stage_summary") or {}
    light = (stages.get("total_light_sleep_time_milli") or 0) / _MILLIS_PER_MIN
    deep = (stages.get("total_slow_wave_sleep_time_milli") or 0) / _MILLIS_PER_MIN
    rem = (stages.get("total_rem_sleep_time_milli") or 0) / _MILLIS_PER_MIN
    total = light + deep + rem
    if total <= 0:
        return None
    return {
        "total_sleep_minutes": round(total),
        "deep_sleep_minutes": round(deep),
        "light_sleep_minutes": round(light),
    }


def get_cycle_today() -> Optional[dict[str, Any]]:
    """Return the current physiological cycle from ``GET /cycle``.

    Returns:
        ``{strain, average_heart_rate}`` (Whoop day strain is 0–21), or ``None``.
    """
    record = _latest_record("/cycle")
    if not record:
        return None
    score = record.get("score") or {}
    return {
        "strain": score.get("strain"),
        "average_heart_rate": score.get("average_heart_rate"),
    }


def get_whoop_summary() -> Optional[dict[str, Any]]:
    """Combine Whoop recovery, sleep, and cycle into the normalized energy dict.

    Returns:
        A dict shaped like the Google Fit summary plus Whoop extras::

            {
              "activity": {"steps": None, "strain": float, "average_heart_rate": int} | None,
              "sleep": {...} | None,
              "resting_heart_rate": float | None,
              "recovery": float | None,   # Whoop recovery % (0–100)
              "hrv": float | None,        # HRV rMSSD (ms)
            }

        Whoop has no step count (it's strain-based), so ``activity.steps`` is ``None``
        and the engine's step modifier simply no-ops. Returns ``None`` if every Whoop
        read came back empty.
    """
    recovery = get_recovery_latest()
    sleep = get_sleep_last_night()
    cycle = get_cycle_today()

    if recovery is None and sleep is None and cycle is None:
        return None

    activity: Optional[dict[str, Any]] = None
    if cycle is not None:
        activity = {
            "steps": None,  # Whoop is strain-based, not a pedometer
            "strain": cycle.get("strain"),
            "average_heart_rate": cycle.get("average_heart_rate"),
        }

    return {
        "activity": activity,
        "sleep": sleep,
        "resting_heart_rate": recovery.get("resting_heart_rate") if recovery else None,
        "recovery": recovery.get("recovery_score") if recovery else None,
        "hrv": recovery.get("hrv_rmssd_milli") if recovery else None,
    }
