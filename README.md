# EnergyScheduler

An AI agent that restructures your Google Calendar around your energy levels.

It reads your **Google Fit** activity, sleep, and heart-rate data to estimate an hourly energy
profile for the day, then uses a **LangGraph** agent (powered by **Groq**) to move deep-work
blocks into your peak hours and low-effort tasks into your dips. No wearable connected? It falls
back to a realistic synthetic energy curve so the demo works immediately.

> 🚧 Work in progress — see the build plan in the commit history.

## Stack

- **FastAPI** — REST backend
- **LangGraph** — the scheduling agent
- **Groq** (`llama-3.3-70b-versatile`) — LLM for classification & restructuring
- **Google Calendar API** + **Google Fit API**
- **Gradio** — frontend UI

## Status

Scaffolding the project. Full setup and deployment instructions to follow.
