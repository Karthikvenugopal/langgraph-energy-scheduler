"""FastAPI backend for EnergyScheduler.

Exposes the optimization agent and supporting data over a small REST API, handles
the Google OAuth round-trip, and mounts the Gradio UI at ``/ui`` so a single
``uvicorn main:app`` process serves both the API and the frontend (which is what
Hugging Face Spaces needs on port 7860).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from dotenv import load_dotenv

# Load .env before anything reads GROQ_API_KEY / OAUTH_REDIRECT_URI.
load_dotenv()

from fastapi import FastAPI, Query, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import HTMLResponse, RedirectResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from agent.graph import run_agent  # noqa: E402
from services import calendar as calendar_service  # noqa: E402
from services import energy as energy_service  # noqa: E402
from services import google_auth  # noqa: E402
from services import wearable as wearable_service  # noqa: E402
from services import whoop_auth  # noqa: E402

app = FastAPI(
    title="EnergyScheduler",
    description="Restructures your calendar around your energy levels.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Pydantic schemas
# --------------------------------------------------------------------------- #
class EventModel(BaseModel):
    """A single calendar block (original or restructured)."""

    title: str
    start: str
    end: str
    duration_minutes: int
    event_type: Optional[str] = None
    description: Optional[str] = None
    original_start: Optional[str] = None
    moved: bool = False


class HealthResponse(BaseModel):
    """Health-check payload."""

    status: str = "ok"
    authenticated: bool


class EnergyProfileResponse(BaseModel):
    """Hourly energy profile plus its provenance."""

    profile: dict[int, int] = Field(..., description="Hour (6–23) -> score (0–100)")
    readiness_score: int
    data_source: str


class FitSummaryResponse(BaseModel):
    """Raw Google Fit data for the day (fields are null without a connected device)."""

    activity: Optional[dict[str, Any]] = None
    sleep: Optional[dict[str, Any]] = None
    resting_heart_rate: Optional[float] = None
    recovery: Optional[float] = None
    hrv: Optional[float] = None
    data_source: str


class OptimizeResponse(BaseModel):
    """Full result of an optimization run."""

    original_schedule: list[EventModel]
    restructured_schedule: list[EventModel]
    reasoning: list[str]
    energy_profile: dict[int, int]
    readiness_score: int
    data_source: str
    changes_made: list[str]


# --------------------------------------------------------------------------- #
# Core endpoints
# --------------------------------------------------------------------------- #
@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness probe; also reports whether a wearable/calendar is connected."""
    connected = google_auth.has_token() or whoop_auth.has_token()
    return HealthResponse(status="ok", authenticated=connected)


@app.post("/optimize", response_model=OptimizeResponse)
def optimize(
    date: Optional[str] = Query(
        default=None, description="ISO date (YYYY-MM-DD); defaults to today."
    )
) -> OptimizeResponse:
    """Run the full LangGraph pipeline and return the restructured schedule.

    Args:
        date: Optional ISO date to optimize; defaults to today.

    Returns:
        The original and restructured schedules, the energy profile, readiness
        score, data source, and a plain-English list of changes.
    """
    state = run_agent(date)

    original = [EventModel(**event) for event in state.get("classified_events", [])]
    restructured = [EventModel(**event) for event in state.get("restructured_schedule", [])]

    changes_made: list[str] = []
    for event in restructured:
        if event.moved and event.original_start:
            changes_made.append(
                f"Moved '{event.title}' from "
                f"{datetime.fromisoformat(event.original_start).strftime('%H:%M')} to "
                f"{datetime.fromisoformat(event.start).strftime('%H:%M')}"
            )

    return OptimizeResponse(
        original_schedule=original,
        restructured_schedule=restructured,
        reasoning=state.get("reasoning", []),
        energy_profile=state.get("energy_profile", {}),
        readiness_score=state.get("readiness_score", 0),
        data_source=state.get("data_source", "synthetic"),
        changes_made=changes_made,
    )


@app.get("/energy-profile", response_model=EnergyProfileResponse)
def energy_profile() -> EnergyProfileResponse:
    """Return today's hourly energy profile (for the frontend chart)."""
    fitness_data, source = wearable_service.get_wearable_summary()
    energy = energy_service.get_energy_profile(fitness_data, source)
    return EnergyProfileResponse(
        profile=energy["profile"],
        readiness_score=energy["readiness_score"],
        data_source=energy["data_source"],
    )


@app.get("/fit-summary", response_model=FitSummaryResponse)
def fit_summary() -> FitSummaryResponse:
    """Return raw wearable data for today (null fields if unavailable)."""
    summary, source = wearable_service.get_wearable_summary()
    summary = summary or {}
    return FitSummaryResponse(
        activity=summary.get("activity"),
        sleep=summary.get("sleep"),
        resting_heart_rate=summary.get("resting_heart_rate"),
        recovery=summary.get("recovery"),
        hrv=summary.get("hrv"),
        data_source=source,
    )


# --------------------------------------------------------------------------- #
# Google OAuth
# --------------------------------------------------------------------------- #
@app.get("/auth/google")
def auth_google() -> Any:
    """Kick off the Google OAuth consent flow (single screen for Calendar + Fit)."""
    try:
        flow = google_auth.build_flow()
    except FileNotFoundError as exc:
        return HTMLResponse(f"<h3>OAuth not configured</h3><p>{exc}</p>", status_code=400)

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return RedirectResponse(auth_url)


@app.get("/auth/callback")
def auth_callback(request: Request) -> Any:
    """Handle the OAuth redirect, exchange the code, and persist the token."""
    try:
        flow = google_auth.build_flow()
        flow.fetch_token(authorization_response=str(request.url))
        google_auth.save_credentials(flow.credentials)
    except Exception as exc:  # noqa: BLE001 - surface a friendly message to the user
        return HTMLResponse(
            f"<h3>Authentication failed</h3><p>{exc}</p>"
            "<p><a href='/ui'>Back to app</a></p>",
            status_code=400,
        )
    return RedirectResponse("/ui")


@app.get("/auth/whoop")
def auth_whoop() -> Any:
    """Kick off the Whoop OAuth consent flow."""
    if not whoop_auth.has_credentials():
        return HTMLResponse(
            "<h3>Whoop not configured</h3><p>Set WHOOP_CLIENT_ID and "
            "WHOOP_CLIENT_SECRET in your environment.</p>",
            status_code=400,
        )
    import secrets

    url = whoop_auth.build_authorize_url(state=secrets.token_urlsafe(16))
    return RedirectResponse(url)


@app.get("/auth/whoop/callback")
def auth_whoop_callback(request: Request) -> Any:
    """Handle the Whoop OAuth redirect, exchange the code, and persist the token."""
    code = request.query_params.get("code")
    if not code:
        return HTMLResponse(
            "<h3>Whoop authentication failed</h3><p>No authorization code returned.</p>"
            "<p><a href='/ui'>Back to app</a></p>",
            status_code=400,
        )
    try:
        whoop_auth.exchange_code(code)
    except Exception as exc:  # noqa: BLE001 - surface a friendly message to the user
        return HTMLResponse(
            f"<h3>Whoop authentication failed</h3><p>{exc}</p>"
            "<p><a href='/ui'>Back to app</a></p>",
            status_code=400,
        )
    return RedirectResponse("/ui")


# --------------------------------------------------------------------------- #
# Mount the Gradio UI at /ui (single process serves API + frontend)
# --------------------------------------------------------------------------- #
@app.get("/")
def root() -> RedirectResponse:
    """Redirect the bare host to the UI."""
    return RedirectResponse("/ui")


try:
    import gradio as gr

    from frontend.app import demo

    app = gr.mount_gradio_app(app, demo, path="/ui")
except Exception as exc:  # noqa: BLE001 - API still works even if the UI fails to load
    print(f"[main] Gradio UI not mounted: {exc}", flush=True)
