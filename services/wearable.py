"""Wearable source dispatcher.

Chooses where the day's biometric summary comes from — Whoop, Google Fit, or
neither (synthetic) — and returns it alongside a source label the rest of the app
uses for the energy ``data_source`` and the UI badge.

Selection is controlled by the ``WEARABLE_SOURCE`` env var:

* ``"auto"`` (default) — prefer Whoop if connected and returning data, else Google
  Fit, else synthetic.
* ``"whoop"`` / ``"google_fit"`` — force a specific source (falls through to
  synthetic if it has no data).
"""

from __future__ import annotations

import os
from typing import Any, Optional

from services import fitness as google_fit
from services import fitness_whoop
from services import google_auth
from services import whoop_auth

_REAL_KEYS = ("activity", "sleep", "resting_heart_rate", "recovery")


def _has_real(summary: Optional[dict[str, Any]]) -> bool:
    """Return ``True`` if the summary carries at least one real biometric signal."""
    return bool(summary) and any(summary.get(key) is not None for key in _REAL_KEYS)


def get_wearable_summary() -> tuple[Optional[dict[str, Any]], str]:
    """Resolve the active wearable summary and its source label.

    Returns:
        ``(summary, source)`` where ``source`` is ``"whoop"``, ``"google_fit"``, or
        ``"synthetic"``. ``summary`` is ``None`` for the synthetic case.
    """
    pref = os.getenv("WEARABLE_SOURCE", "auto").lower()

    if pref in ("whoop", "auto") and whoop_auth.has_token():
        summary = fitness_whoop.get_whoop_summary()
        if _has_real(summary):
            return summary, "whoop"

    if pref in ("google_fit", "auto") and google_auth.has_token():
        summary = google_fit.get_fitness_summary()
        if _has_real(summary):
            return summary, "google_fit"

    return None, "synthetic"
