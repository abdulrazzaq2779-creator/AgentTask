# 🔁 FailureLoop — Incident Recurrence Prevention Agent

FailureLoop remembers past incidents as structured **failure fingerprints**,
matches new incidents by their **underlying causal chain** (not error text),
recommends fixes **proven to work**, and warns about fixes that **failed
before**.

Stack: **FastAPI + React (Vite/Tailwind)** console, Python engine
(fingerprint extraction → dual recall → LLM-judged matching → outcome-aware
fix ranking → stateless-vs-memory recommendations), SQLite storage, optional
Hindsight Cloud memory.

## Quick start (React console — recommended)

```bash
# 1. Configure (one time): put your free Groq key in .env   (console.groq.com)
#    LLM_API_KEY=gsk_...

# 2. Run everything (API on :8000 + web UI on :5173)
bash run.sh
```

Open **http://localhost:5173** — the app comes pre-loaded: 8 historical
postmortems are auto-seeded into memory on API startup, plus 4 live demo
scenarios in the picker. **No setup clicks, no empty state.**

Run the pieces separately:

```bash
python -m uvicorn failureloop.api:app --port 8000   # API (auto-seeds history)
cd frontend && npm install && npm run dev           # web console
```

## API endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | config, memory mode, incident count |
| GET | `/api/incidents` | all retained incidents (auto-seeded) |
| GET | `/api/incidents/{id}` | full fingerprint + raw text |
| GET | `/api/demo-incidents` | the 4 live demo scenarios |
| POST | `/api/analyze` | full pipeline: extract → recall → judge → recommend |

Interactive docs: `http://localhost:8000/docs`

## Legacy Streamlit UI

```bash
streamlit run failureloop/ui.py
```

## CLI (Stage 2 scripts)

```bash
python scripts/load_history.py --fresh                 # seed the bank
python scripts/analyze_incident.py --demo 1 --mode both  # side-by-side demo
python scripts/evaluate.py                             # pattern/fix accuracy
```

## Tests

```bash
python -m pytest tests/ -q     # 33 tests, LLM + Hindsight fully mocked
```

## Architecture

```
React console (frontend/, :5173)
      │  /api proxy
      ▼
FastAPI (failureloop/api.py, :8000)
      ▼
FailureLoop engine
 ├─ extraction/   raw text → FailureFingerprint (+ notes, field confidence)
 ├─ matching/     dual recall (causal + surface) → batched LLM judge →
 │                component scores → partial-match flagging → cold start
 ├─ matching/fix_ranker   proven / failed / conflicting fixes + evidence IDs
 └─ recommend/    stateless vs memory (Hindsight reflect), honest confidence
      ▼
SQLite (failureloop.db)   ·   Hindsight Cloud (optional)
```

## Safety

FailureLoop assists on-call engineers; it never executes production changes.
Memory-based claims cite incident IDs; uncited claims are labeled general
reasoning. Weak evidence lowers confidence instead of inflating it.
