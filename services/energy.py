"""Energy score engine.

Turns Google Fit data into an hourly energy profile (06:00–23:00). When real Fit
data is present, a base circadian curve is nudged by sleep, activity, and recovery
(resting heart-rate) signals. When no Fit data is available, a synthetic curve with
small per-call random noise is used instead so the demo always has something to show.

To use a different wearable, replace :func:`services.fitness.get_fitness_summary`
with an Oura/Whoop call and keep the same ``{activity, sleep, resting_heart_rate}``
shape — this engine will work unchanged.
"""

from __future__ import annotations

import random
import statistics
from typing import Any, Optional

# Base circadian curve: moderate wakeup, mid-morning peak, post-lunch dip,
# secondary afternoon peak, gradual evening decline. Hours are 6 (06:00)–23 (23:00).
BASE_CURVE: dict[int, int] = {
    6: 60,
    7: 68,
    8: 78,
    9: 85,
    10: 88,
    11: 80,
    12: 64,
    13: 50,   # post-lunch dip
    14: 54,
    15: 68,
    16: 72,   # secondary afternoon peak
    17: 64,
    18: 56,
    19: 50,
    20: 44,
    21: 38,
    22: 32,
    23: 28,
}

MORNING_HOURS = range(6, 12)      # 06:00–11:59
AFTERNOON_HOURS = range(12, 18)   # 12:00–17:59


def _clamp(score: float) -> int:
    """Clamp a score into the inclusive 0–100 range and round to an int."""
    return int(max(0, min(100, round(score))))


def _has_real_data(fitness_data: Optional[dict[str, Any]]) -> bool:
    """Return ``True`` if ``fitness_data`` carries at least one real Fit signal.

    Args:
        fitness_data: The combined ``{activity, sleep, resting_heart_rate}`` summary
            (any field may be ``None``), or ``None``.
    """
    if not fitness_data:
        return False
    return any(
        fitness_data.get(key) is not None
        for key in ("activity", "sleep", "resting_heart_rate", "recovery", "hrv")
    )


def _compute_readiness(
    recovery: Optional[float],
    sleep: Optional[dict[str, Any]],
    activity: Optional[dict[str, Any]],
    resting_hr: Optional[float],
) -> int:
    """Derive an overall 0–100 readiness score from real biometric signals.

    If a Whoop recovery score is present it is used directly — it *is* a calibrated
    readiness number. Otherwise readiness starts at a neutral 50 and adds/subtracts
    contributions from sleep duration, daily activity, and resting HR; each missing
    signal simply contributes nothing.

    Returns:
        The clamped readiness score.
    """
    if recovery is not None:
        return _clamp(recovery)

    score = 50.0

    if sleep is not None:
        total = sleep.get("total_sleep_minutes") or 0
        if total >= 480:
            score += 18
        elif total >= 420:
            score += 10
        elif total >= 360:
            score += 2
        else:
            score -= 18

    if activity is not None:
        steps = activity.get("steps") or 0
        if steps >= 8000:
            score += 12
        elif steps >= 4000:
            score += 4
        elif steps < 2000:
            score -= 8

    if resting_hr is not None:
        if resting_hr < 55:
            score += 10
        elif resting_hr <= 65:
            score += 4
        elif resting_hr <= 75:
            score -= 2
        else:
            score -= 14

    return _clamp(score)


def _profile_from_fitness(fitness_data: dict[str, Any]) -> dict[int, int]:
    """Apply sleep/activity/recovery modifiers to the base curve.

    Modifiers:
      * Whoop recovery ≥ 67 (green) → +6 to every hour; < 34 (red) → −12 to every hour
      * sleep < 360 min → −15 to every hour; > 480 min → +10 to every hour
      * steps > 8000 → +8 to afternoon hours; < 2000 → −8 to afternoon hours
        (skipped when steps is unavailable, e.g. Whoop, which is strain-based)
      * resting HR > 75 → −10 to morning hours

    Returns:
        The modified, clamped hourly profile.
    """
    recovery = fitness_data.get("recovery")
    sleep = fitness_data.get("sleep")
    activity = fitness_data.get("activity")
    resting_hr = fitness_data.get("resting_heart_rate")

    profile = dict(BASE_CURVE)

    # Recovery modifier (Whoop) — applies to the whole day.
    if recovery is not None:
        if recovery >= 67:
            profile = {hour: score + 6 for hour, score in profile.items()}
        elif recovery < 34:
            profile = {hour: score - 12 for hour, score in profile.items()}

    # Sleep modifier (applies to the whole day).
    if sleep is not None:
        total = sleep.get("total_sleep_minutes") or 0
        if total < 360:
            profile = {hour: score - 15 for hour, score in profile.items()}
        elif total > 480:
            profile = {hour: score + 10 for hour, score in profile.items()}

    # Activity modifier (afternoon only). Skipped when steps aren't reported.
    if activity is not None and activity.get("steps") is not None:
        steps = activity["steps"]
        if steps > 8000:
            for hour in AFTERNOON_HOURS:
                profile[hour] += 8
        elif steps < 2000:
            for hour in AFTERNOON_HOURS:
                profile[hour] -= 8

    # Resting-HR modifier (morning only).
    if resting_hr is not None and resting_hr > 75:
        for hour in MORNING_HOURS:
            profile[hour] -= 10

    return {hour: _clamp(score) for hour, score in profile.items()}


def _synthetic_profile() -> dict[int, int]:
    """Return the base curve with small per-hour random noise (±5)."""
    return {hour: _clamp(score + random.uniform(-5, 5)) for hour, score in BASE_CURVE.items()}


def get_energy_profile(
    fitness_data: Optional[dict[str, Any]] = None, source: Optional[str] = None
) -> dict[str, Any]:
    """Build the hourly energy profile, readiness score, and data source.

    Args:
        fitness_data: Optional combined wearable summary
            ``{activity, sleep, resting_heart_rate, recovery, hrv}`` (each field may
            be ``None``). When ``None`` or all-``None``, the synthetic curve is used.
        source: Label for the data's origin (``"whoop"`` / ``"google_fit"``); used as
            ``data_source`` when real data is present.

    Returns:
        A dict with:
          * ``profile``: ``{hour (6–23) -> score 0–100}``
          * ``readiness_score``: overall 0–100 day score
          * ``data_source``: the ``source`` label, or ``"synthetic"``
    """
    if _has_real_data(fitness_data):
        assert fitness_data is not None  # for type-checkers; guarded by _has_real_data
        profile = _profile_from_fitness(fitness_data)
        readiness = _compute_readiness(
            fitness_data.get("recovery"),
            fitness_data.get("sleep"),
            fitness_data.get("activity"),
            fitness_data.get("resting_heart_rate"),
        )
        data_source = source or "wearable"
    else:
        profile = _synthetic_profile()
        readiness = _clamp(statistics.mean(profile.values()) + random.uniform(-4, 4))
        data_source = "synthetic"

    return {
        "profile": profile,
        "readiness_score": readiness,
        "data_source": data_source,
    }
