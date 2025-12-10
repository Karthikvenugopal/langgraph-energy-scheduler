"""Gradio frontend for EnergyScheduler.

Shows the original vs. AI-restructured schedule side by side, a color-coded hourly
energy bar chart, a readiness score, a Google-Fit-vs-synthetic badge, and the
agent's plain-English reasoning for every change.

Backend access is dual-mode:

* If ``BACKEND_URL`` is set, the UI calls the FastAPI REST endpoints over HTTP
  (useful for ``python frontend/app.py`` against a separate ``uvicorn`` server).
* Otherwise it calls the agent/services **in-process**, which is what happens when
  the UI is mounted inside FastAPI (Docker / Hugging Face Spaces) — this avoids a
  single-worker server trying to make an HTTP call to itself.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Optional

import gradio as gr
import pandas as pd
import requests

from services import calendar as calendar_service
from services import google_auth

# When set, the UI talks to the REST API; otherwise it runs the agent in-process.
BACKEND_URL = os.getenv("BACKEND_URL")

ZONE_HIGH = "High (>70)"
ZONE_MED = "Medium (50-70)"
ZONE_LOW = "Low (<50)"
ZONE_COLORS = {ZONE_HIGH: "#22c55e", ZONE_MED: "#eab308", ZONE_LOW: "#ef4444"}


# --------------------------------------------------------------------------- #
# Backend access (HTTP or in-process)
# --------------------------------------------------------------------------- #
def _optimize(date: Optional[str]) -> dict[str, Any]:
    """Run an optimization, via HTTP or in-process depending on ``BACKEND_URL``."""
    if BACKEND_URL:
        params = {"date": date} if date else None
        response = requests.post(f"{BACKEND_URL}/optimize", params=params, timeout=180)
        response.raise_for_status()
        return response.json()

    from agent.graph import run_agent

    state = run_agent(date or None)
    restructured = state.get("restructured_schedule", [])
    changes = [
        f"Moved '{e['title']}' from "
        f"{_fmt(e['original_start'])} to {_fmt(e['start'])}"
        for e in restructured
        if e.get("moved") and e.get("original_start")
    ]
    return {
        "original_schedule": state.get("classified_events", []),
        "restructured_schedule": restructured,
        "reasoning": state.get("reasoning", []),
        "energy_profile": state.get("energy_profile", {}),
        "readiness_score": state.get("readiness_score", 0),
        "data_source": state.get("data_source", "synthetic"),
        "changes_made": changes,
    }


def _get_energy() -> dict[str, Any]:
    """Fetch today's energy profile, via HTTP or in-process."""
    if BACKEND_URL:
        response = requests.get(f"{BACKEND_URL}/energy-profile", timeout=60)
        response.raise_for_status()
        return response.json()

    from services import energy as energy_service
    from services import fitness as fitness_service

    return energy_service.get_energy_profile(fitness_service.get_fitness_summary())


# --------------------------------------------------------------------------- #
# Rendering helpers
# --------------------------------------------------------------------------- #
def _fmt(iso: str) -> str:
    """Format an ISO timestamp as ``HH:MM``."""
    return datetime.fromisoformat(iso).strftime("%H:%M")


def _energy_dataframe(profile: dict[Any, Any]) -> pd.DataFrame:
    """Build a tidy DataFrame (hour, energy, zone) for the bar chart."""
    rows = []
    for hour, score in sorted((int(h), int(s)) for h, s in profile.items()):
        if score > 70:
            zone = ZONE_HIGH
        elif score >= 50:
            zone = ZONE_MED
        else:
            zone = ZONE_LOW
        rows.append({"hour": hour, "energy": score, "zone": zone})
    return pd.DataFrame(rows)


def _badge_html(data_source: str) -> str:
    """Render the data-source badge."""
    if data_source == "google_fit":
        return (
            "<span style='background:#dcfce7;color:#166534;padding:6px 12px;"
            "border-radius:999px;font-weight:600;'>🟢 Powered by Google Fit</span>"
        )
    return (
        "<span style='background:#fef9c3;color:#854d0e;padding:6px 12px;"
        "border-radius:999px;font-weight:600;'>🟡 Using synthetic energy data</span>"
    )


def _readiness_html(score: int) -> str:
    """Render the large readiness number with its qualitative label."""
    if score > 75:
        label, color = "Great day for deep work", "#16a34a"
    elif score >= 50:
        label, color = "Moderate energy day", "#ca8a04"
    else:
        label, color = "Recovery day — protect your focus time", "#dc2626"
    return (
        "<div style='text-align:center;'>"
        f"<div style='font-size:64px;font-weight:800;line-height:1;color:{color};'>{score}</div>"
        "<div style='font-size:13px;color:#6b7280;letter-spacing:.05em;'>READINESS</div>"
        f"<div style='font-size:16px;font-weight:600;margin-top:4px;'>{label}</div>"
        "</div>"
    )


def _connection_html() -> str:
    """Render the Google connection status / connect link."""
    if google_auth.has_token():
        name = calendar_service.get_calendar_name() or "your Google Calendar"
        return (
            f"<div style='padding:8px 0;'>✅ Connected to <b>{name}</b> "
            "— using your real calendar and Google Fit data.</div>"
        )
    auth_url = f"{BACKEND_URL}/auth/google" if BACKEND_URL else "/auth/google"
    return (
        "<div style='padding:8px 0;'>"
        f"<a href='{auth_url}' style='background:#2563eb;color:white;padding:8px 16px;"
        "border-radius:8px;text-decoration:none;font-weight:600;'>Connect Google</a>"
        "<span style='margin-left:10px;color:#6b7280;'>Using demo data — connect to use "
        "your real calendar &amp; Fit data.</span></div>"
    )


def _render_schedule(events: list[dict[str, Any]]) -> str:
    """Render a list of events as a Markdown schedule."""
    if not events:
        return "_No events._"
    lines = []
    for event in sorted(events, key=lambda e: e["start"]):
        moved = " · 🔀 _moved_" if event.get("moved") else ""
        etype = f" · `{event['event_type']}`" if event.get("event_type") else ""
        lines.append(
            f"- **{_fmt(event['start'])}–{_fmt(event['end'])}** "
            f"{event['title']}{etype}{moved}"
        )
    return "\n".join(lines)


def _render_reasoning(reasoning: list[str]) -> str:
    """Render the agent's reasoning bullets as Markdown."""
    if not reasoning:
        return "_Click **Optimize My Day** to see the agent's reasoning._"
    return "\n".join(f"- {line}" for line in reasoning)


# --------------------------------------------------------------------------- #
# Event handlers
# --------------------------------------------------------------------------- #
def initial_view() -> tuple[Any, ...]:
    """Populate the UI on load: energy chart, badge, readiness, original schedule."""
    energy = _get_energy()
    profile = energy.get("profile", {})
    df = _energy_dataframe(profile)
    original = calendar_service.get_events_today()
    return (
        _connection_html(),
        _badge_html(energy.get("data_source", "synthetic")),
        _readiness_html(int(energy.get("readiness_score", 0))),
        df,
        _render_schedule(original),
        _render_reasoning([]),
    )


def on_optimize(date: str) -> tuple[Any, ...]:
    """Run the optimization and update every output panel."""
    result = _optimize(date.strip() or None)
    df = _energy_dataframe(result.get("energy_profile", {}))
    return (
        _badge_html(result.get("data_source", "synthetic")),
        _readiness_html(int(result.get("readiness_score", 0))),
        df,
        _render_schedule(result.get("original_schedule", [])),
        _render_schedule(result.get("restructured_schedule", [])),
        _render_reasoning(result.get("reasoning", [])),
    )


# --------------------------------------------------------------------------- #
# Build the Blocks UI
# --------------------------------------------------------------------------- #
def _build_barplot(df: pd.DataFrame) -> gr.BarPlot:
    """Create the energy BarPlot, using an explicit color map where supported."""
    common = dict(
        value=df,
        x="hour",
        y="energy",
        color="zone",
        title="Hourly energy profile",
        height=320,
    )
    try:
        return gr.BarPlot(**common, color_map=ZONE_COLORS)
    except TypeError:
        # Older Gradio without color_map on BarPlot — fall back to default palette.
        return gr.BarPlot(**common)


with gr.Blocks(title="EnergyScheduler") as demo:
    gr.Markdown(
        "# ⚡ EnergyScheduler\n"
        "Restructures your calendar so deep work lands in your peak hours and "
        "low-effort tasks fall into your dips."
    )
    connection = gr.HTML(_connection_html())

    with gr.Row():
        with gr.Column(scale=2):
            energy_plot = _build_barplot(_energy_dataframe({}))
        with gr.Column(scale=1):
            badge = gr.HTML(_badge_html("synthetic"))
            readiness = gr.HTML(_readiness_html(0))

    with gr.Row():
        date_box = gr.Textbox(
            label="Date (YYYY-MM-DD, blank = today)", placeholder="2026-06-25", scale=3
        )
        optimize_btn = gr.Button("⚡ Optimize My Day", variant="primary", scale=1)

    with gr.Row():
        with gr.Column():
            gr.Markdown("### 📋 Original schedule")
            original_md = gr.Markdown("_Loading…_")
        with gr.Column():
            gr.Markdown("### ✨ Restructured schedule")
            restructured_md = gr.Markdown("_Click **Optimize My Day**._")

    gr.Markdown("### 🧠 Agent reasoning")
    reasoning_md = gr.Markdown(_render_reasoning([]))

    demo.load(
        fn=initial_view,
        outputs=[connection, badge, readiness, energy_plot, original_md, reasoning_md],
    )
    optimize_btn.click(
        fn=on_optimize,
        inputs=[date_box],
        outputs=[badge, readiness, energy_plot, original_md, restructured_md, reasoning_md],
    )


if __name__ == "__main__":
    # Standalone dev mode. Set BACKEND_URL to hit a separate FastAPI server;
    # otherwise the agent runs in-process here.
    demo.launch(server_name="0.0.0.0", server_port=7861)
