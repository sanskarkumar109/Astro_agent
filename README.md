# AstroAgent

AstroAgent is a compact take-home implementation of a chat-based astrology companion. It includes a streaming FastAPI backend, a React chat UI, real ephemeris-backed chart tools through `pyswisseph`, and a one-command evaluation harness with a versioned golden set.

## Quick Start

Backend:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn backend.app.main:app --reload
```

Optional Groq AI mode:

```powershell
$env:GROQ_API_KEY="your_groq_api_key_here"
$env:GROQ_MODEL="llama-3.3-70b-versatile"
python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

Without `GROQ_API_KEY`, the app still works through deterministic fallback responses. With the key, the agent first calls the chart/transit/knowledge tools, then asks Groq to compose a warmer answer grounded only in that tool output.

Frontend:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. The frontend expects the API at `http://127.0.0.1:8000`; set `VITE_API_URL` if you use another port.

Evaluation:

```powershell
python eval/run_eval.py
```

The runner prints a scorecard and updates:

- `eval/results/latest_scorecard.md`
- `eval/results/results_log.csv`

## Architecture

The backend exposes a small API:

- `POST /chat` streams newline-delimited JSON events.
- `GET /graph` returns the graph shape.
- `POST /tools/compute_birth_chart`
- `POST /tools/get_daily_transits`
- `POST /tools/geocode_place`
- `POST /tools/knowledge_lookup`

Agent graph:

```text
user message
  -> router / reasoning node
  -> tool node when chart, transit, or knowledge context is needed
  -> reasoning node observes tool output
  -> response node streams final answer
```

The code uses a real LangGraph `StateGraph` in `backend/app/agent.py`. The graph routes through reasoning, tool, guardrail, and response nodes, and the same node boundaries are exposed through `/graph`.

## Tools

`compute_birth_chart(date, time, place)` uses Swiss Ephemeris via `pyswisseph` to compute tropical planetary longitudes, Placidus houses, and chart angles. If `pyswisseph` is not installed, the tool fails loudly rather than inventing positions.

`get_daily_transits(date, natal_chart)` computes current transits with Swiss Ephemeris and returns simple major same-planet natal aspects.

`geocode_place(place)` uses a curated city table for common demo locations and accepts `latitude, longitude` input. This avoids fake geocoding without requiring an external network service.

`knowledge_lookup(query)` searches curated notes in `backend/knowledge/astrology_notes.md`.

## Frontend

The React UI includes:

- Birth-details form with validation.
- Streamed assistant responses.
- Visible tool activity.
- Loading and error states.
- Conversation and birth-detail persistence through `localStorage`.
- Responsive layout for desktop and mobile.

## Guardrails

AstroAgent treats astrology as reflection, not certainty. Medical, legal, and financial prompts receive a cautionary response and are covered in the golden set.

## Known Limitations

- The current agent response layer is rule-based rather than LLM-composed, which keeps eval deterministic but limits nuance.
- Geocoding is intentionally small and offline-first; production would use a proper geocoder with timezone resolution.
- Transit interpretation is basic and focuses on same-planet major aspects.
- The eval runner disables live Groq calls by default so deterministic tool and routing regressions remain fast to catch.
