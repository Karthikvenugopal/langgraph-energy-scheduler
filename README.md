# ⚡ EnergyScheduler

An AI agent that **restructures your Google Calendar around your energy levels**.

EnergyScheduler reads your **Google Fit** activity, sleep, and heart-rate data to estimate an
hourly energy profile for the day, then runs a **LangGraph** agent (powered by **Groq**) that
moves deep-work blocks into your peak hours and pushes low-effort tasks into your dips. No
wearable connected? It falls back to a realistic **synthetic energy curve**, so the whole thing
works immediately with zero setup.

Everything in the stack is **free to run and deploy**.

---

## Features

- 🔋 **Energy-aware scheduling** — deep work → peak energy, admin/breaks → dips.
- ⌚ **Real Google Fit data** (steps, active minutes, sleep stages, resting HR) with a graceful
  synthetic fallback.
- 🧠 **LangGraph agent** — a transparent 5-node pipeline you can watch run in stdout.
- 🗓️ **Google Calendar** integration with a built-in demo schedule (no auth required).
- 🖥️ **Gradio UI** — original vs. restructured schedule, color-coded energy chart, readiness
  score, and the agent's reasoning for every move.
- 🆓 **Free LLM** via Groq (`llama-3.3-70b-versatile`), and one-click Hugging Face Spaces deploy.
- 🔌 **Runs with no keys at all** — without `GROQ_API_KEY` it uses deterministic
  classification + rule-based scheduling.

---

## Stack

| Layer       | Tech                                            |
|-------------|-------------------------------------------------|
| Backend     | FastAPI + Uvicorn                               |
| Agent       | LangGraph                                        |
| LLM         | Groq — `llama-3.3-70b-versatile` (via `langchain-groq`) |
| Data        | Google Calendar API + Google Fit (Fitness) API  |
| Frontend    | Gradio                                           |
| Deploy      | Docker → Hugging Face Spaces                     |

---

## Project structure

```
.
├── main.py                 # FastAPI app + Pydantic schemas, mounts the Gradio UI at /ui
├── agent/
│   ├── graph.py            # LangGraph StateGraph + run_agent()
│   ├── nodes.py            # fetch_data → classify_events → analyze_fit → restructure → format_output
│   ├── state.py            # TypedDict agent state
│   └── tools.py            # classify_event_type, score_event_fit, find_best_slot
├── services/
│   ├── google_auth.py      # shared OAuth + get_google_service()
│   ├── calendar.py         # Google Calendar client + demo events
│   ├── fitness.py          # Google Fit client (activity / sleep / heart rate)
│   └── energy.py           # energy score engine (real + synthetic)
├── frontend/
│   └── app.py              # Gradio UI
├── requirements.txt
├── .env.example
├── Dockerfile
└── README.md
```

---

## How it works

1. **fetch_data** — pulls the day's calendar events, your latest Google Fit summary, and builds
   an hourly energy profile (06:00–23:00).
2. **classify_events** — labels each event `DEEP_WORK` / `MEETING` / `ADMIN` / `BREAK` /
   `PERSONAL` (Groq, with a keyword fallback).
3. **analyze_fit** — scores each event against the energy at its slot and flags mismatches
   (e.g. deep work scheduled during a dip).
4. **restructure** — proposes a new schedule: meetings stay put (they involve other people),
   solo blocks move into better-matched energy windows, with a plain-English reason for each move.
5. **format_output** — emits clean JSON plus human-readable reasoning bullets.

Every node logs a `[node] …` line to stdout so you can follow the reasoning chain.

---

## Quick start (local)

```bash
# 1. Clone and enter the project
git clone <your-repo-url> energyscheduler && cd energyscheduler

# 2. Create a virtual environment and install deps
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. Configure your Groq key (optional — the app runs without it too)
cp .env.example .env
# edit .env and set GROQ_API_KEY=...

# 4. Run the app (API + UI in one process)
uvicorn main:app --reload --port 8000
```

Open:

- **UI:** http://localhost:8000/ui
- **API docs:** http://localhost:8000/docs
- **Health:** http://localhost:8000/health

> The app works out of the box in **demo mode** — a realistic sample calendar plus a synthetic
> energy curve — with **no Google account and no API keys**.

### Running the frontend separately (optional)

The UI is already served at `/ui`. If you want to iterate on the frontend on its own port:

```bash
# Terminal 1: the API
uvicorn main:app --port 8000

# Terminal 2: the standalone UI, pointed at the API
BACKEND_URL=http://localhost:8000 python frontend/app.py   # serves on :7861
```

Without `BACKEND_URL`, the standalone UI runs the agent **in-process** (no server needed).

---

## Get a free Groq API key

1. Sign up at **https://console.groq.com**.
2. Create an API key.
3. Put it in `.env`: `GROQ_API_KEY=gsk_...`

Groq's free tier is plenty for this app. The model is `llama-3.3-70b-versatile` (override with
`GROQ_MODEL` in `.env`).

---

## Connect Google Calendar + Google Fit

A **single consent screen** authorizes both Calendar (read-only) and Fit (activity / sleep /
heart-rate read).

1. **Create a Google Cloud project** — https://console.cloud.google.com → *New Project*.
2. **Enable APIs** — *APIs & Services → Library* → enable **Google Calendar API** and
   **Fitness API**.
3. **Configure the OAuth consent screen** — *APIs & Services → OAuth consent screen*:
   - User type **External**, fill in the app name/email.
   - Add the scopes:
     `calendar.readonly`, `fitness.activity.read`, `fitness.sleep.read`,
     `fitness.heart_rate.read`.
   - Add your Google account as a **Test user**.
4. **Create OAuth credentials** — *APIs & Services → Credentials → Create credentials → OAuth
   client ID → Web application*:
   - **Authorized redirect URI:** `http://localhost:8000/auth/callback`
   - Download the JSON and save it as **`credentials.json`** in the project root.
5. **Authorize** — start the app, open the UI, click **Connect Google**, and approve. The token
   is saved to `token.json` and reused automatically.

> `credentials.json`, `token.json`, and `.env` are git-ignored — they never get committed.

---

## Connecting a Google Fit device for real data

To get **real** energy scores instead of the synthetic curve:

- Install **Google Fit** on an **Android** phone (or connect any Google Fit-compatible
  wearable — Wear OS, Fitbit-via-Health-Connect, etc.) and let it record activity and sleep.
- Re-authorize if needed so the Fit scopes are granted.
- The app pulls **yesterday's** activity and **last night's** sleep/HR; the energy engine then
  switches the badge to **🟢 Powered by Google Fit**.

**iPhone users:** Google Fit doesn't collect data on iOS, so the Fit calls return `None` and the
app automatically falls back to the synthetic curve. See the next section for alternatives.

> ⚠️ **Heads-up:** Google has announced deprecation of the Google Fit REST APIs (in favor of
> Health Connect on-device). This project implements the Fit REST integration as specified and
> still reads existing Fit data; for a long-term build, plan to migrate the `services/fitness.py`
> data source.

---

## Connecting real wearable data (Oura, Whoop, Apple Health)

The energy engine only needs a dict shaped like:

```python
{
    "activity": {"steps": int, "active_minutes": int, "calories_burned": float, "distance_meters": float},
    "sleep": {"total_sleep_minutes": int, "deep_sleep_minutes": int, "light_sleep_minutes": int},
    "resting_heart_rate": float,
}
```

To swap in a different source, replace `services.fitness.get_fitness_summary()` with your own
call and keep that shape — `services/energy.py` works unchanged.

- **Oura** — use the [Oura API v2](https://cloud.ouraring.com/v2/docs) with a personal access
  token: map `daily_activity` → activity, `daily_sleep`/`sleep` → sleep, and `daily_readiness`'s
  resting HR → `resting_heart_rate`.
- **Whoop** — use the [Whoop API](https://developer.whoop.com/) recovery + sleep + workout
  endpoints (OAuth2).
- **Apple Health** — export via the Health app or a companion app that exposes HealthKit data
  (e.g. an iOS shortcut or a small bridge app), then feed the daily metrics into the same dict.

---

## API reference

| Method | Path              | Description                                                   |
|--------|-------------------|---------------------------------------------------------------|
| GET    | `/health`         | Liveness + whether Google is connected.                       |
| POST   | `/optimize?date=` | Run the full agent; returns original + restructured schedule. |
| GET    | `/energy-profile` | Today's hourly energy profile and data source.                |
| GET    | `/fit-summary`    | Raw Google Fit data for today (nulls if unavailable).         |
| GET    | `/auth/google`    | Start the Google OAuth flow.                                   |
| GET    | `/auth/callback`  | OAuth redirect handler (saves `token.json`).                   |
| GET    | `/ui`             | The Gradio UI.                                                 |

---

## Deploy to Hugging Face Spaces

The app is a single Docker container that serves the API and the Gradio UI on port **7860**.

1. **Create a Space** — https://huggingface.co/new-space → SDK **Docker** (blank template).
2. **Push this repo** to the Space (it contains the `Dockerfile`):
   ```bash
   git remote add space https://huggingface.co/spaces/<your-username>/energyscheduler
   git push space main
   ```
3. **Set the secret** — Space *Settings → Variables and secrets* → add **`GROQ_API_KEY`**.
4. The Space builds and serves at `https://<your-username>-energyscheduler.hf.space` (UI at
   `/ui`, root redirects there).

> **OAuth in a Space:** Google OAuth needs a verified, HTTPS redirect URI registered for your
> OAuth client. A public Space URL won't be authorized out of the box, so the deployed Space
> **defaults to demo mode** (sample calendar + synthetic energy). For real Calendar/Fit data,
> run locally with `credentials.json`, or register your Space's
> `https://…hf.space/auth/callback` URL as an authorized redirect URI and re-deploy.

---

## Environment variables

| Variable             | Required | Default                       | Purpose                                   |
|----------------------|----------|-------------------------------|-------------------------------------------|
| `GROQ_API_KEY`       | No*      | —                             | Groq LLM. Omitted → heuristic fallbacks.  |
| `GROQ_MODEL`         | No       | `llama-3.3-70b-versatile`     | Override the Groq model.                   |
| `BACKEND_URL`        | No       | (unset → in-process)          | Make the standalone UI use the REST API.  |
| `OAUTH_REDIRECT_URI` | No       | `http://localhost:8000/auth/callback` | OAuth callback URL.               |

\* The app runs without it; set it for LLM-powered classification and restructuring.

---

## License

MIT — personal project, use freely.
