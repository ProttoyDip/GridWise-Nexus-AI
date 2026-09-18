# GridWise Nexus AI

**An LLM-assisted campus energy optimization agent** — built for the BUP CSE Fest 2026 Hackathon (Online Preliminary), in association with Poridhi.

GridWise interprets natural-language operator notes (e.g. *"Solar output will drop to about 20% from 1 PM to 3 PM"*), converts them into structured, machine-checkable directives, validates them deterministically, and produces a cost-minimizing 24-hour campus energy schedule that respects every applicable constraint.

---

## Status

🚧 **Work in progress.** The API layer, request/response schema, and input validation are implemented. The LLM interpreter, deterministic guardrails, optimizer, and final verifier are scaffolded but not yet implemented — `POST /optimize-energy` currently returns a structurally valid **placeholder** response (every note reported as `no_op`, all demand bought from the grid).

| Component | Status |
| --- | --- |
| `GET /health` | ✅ Implemented |
| `POST /optimize-energy` — request/response schema & validation | ✅ Implemented |
| `POST /optimize-energy` — business logic | 🚧 Placeholder only |
| LLM operator-note interpreter | ⬜ Not started |
| Deterministic guardrails | ⬜ Not started |
| Energy schedule optimizer | ⬜ Not started |
| Final schedule verifier | ⬜ Not started |
| Deployment / Docker | ⬜ Not started |

---

## Architecture

Operator notes are natural language and are never trusted directly as math. They flow through a fixed pipeline before touching the optimizer:

```text
                 ┌──────────────────┐
 Energy Data +   │   LLM Interpreter │   Converts each operator note into
 Operator Notes ─▶│  (untrusted out) │──▶ a candidate structured directive
                 └──────────────────┘   (or no_op)
                           │
                           ▼
                 ┌──────────────────┐
                 │ Guardrail Validator│  Deterministic checks: allowed
                 │                  │──▶ directive types, hour ranges,
                 └──────────────────┘   numeric bounds, applies semantics
                           │
                           ▼
                 ┌──────────────────┐
                 │   Math Optimizer   │  Builds the 24h schedule that
                 │                  │──▶ minimizes grid cost subject to
                 └──────────────────┘   every validated directive
                           │
                           ▼
                 ┌──────────────────┐
                 │  Final Verifier    │  Replays the schedule against all
                 │                  │──▶ constraints before responding
                 └──────────────────┘
                           │
                           ▼
                    API Response
        (directive_interpretation + hourly_plan)
```

## Project Structure

```text
GridWise-Nexus-AI/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI app entrypoint, router registration
│   │   ├── api/
│   │   │   ├── health.py      # GET /health
│   │   │   └── optimize.py    # POST /optimize-energy (pipeline orchestration)
│   │   ├── models/
│   │   │   ├── request.py     # Request schema + validation (Pydantic)
│   │   │   └── response.py    # Response schema (Pydantic)
│   │   ├── llm/                # LLM provider client + prompt templates
│   │   ├── guardrails/         # Deterministic directive validation
│   │   ├── optimizer/          # 24-hour cost-minimizing scheduler
│   │   └── verifier/           # Final schedule replay/validation
│   ├── requirements.txt
│   └── .env.example
└── README.md
```

## API Reference

### `GET /health`

Readiness probe. Returns `200 OK` once the service is ready.

```json
{ "status": "ok" }
```

### `POST /optimize-energy`

Accepts a 24-hour energy scenario plus 1–3 operator notes; returns the interpreted directives and the optimized 24-hour schedule.

**Request** (abridged — see `app/models/request.py` for full validation rules):

```json
{
  "scenario_id": "GRID-101",
  "operator_notes": [
    "Solar output will drop to about 20% from 1 PM to 3 PM."
  ],
  "hours": [
    { "hour": 0, "demand_kwh": 180, "solar_kwh": 0, "tariff_bdt_per_kwh": 7 }
    // ... exactly 24 entries, hours 0–23, each unique
  ],
  "battery": {
    "capacity_kwh": 500,
    "initial_energy_kwh": 200,
    "minimum_energy_kwh": 50,
    "max_charge_kwh_per_hour": 100,
    "max_discharge_kwh_per_hour": 100
  }
}
```

**Response** (abridged — see `app/models/response.py`):

```json
{
  "scenario_id": "GRID-101",
  "directive_interpretation": [
    {
      "note_index": 0,
      "applies": true,
      "directive_type": "solar_reduction",
      "structured_adjustment": { "hours": [13, 14], "factor": 0.2 },
      "explanation": "Solar availability is reduced during the stated window."
    }
  ],
  "hourly_plan": [ /* 24 hourly entries */ ],
  "total_grid_kwh": 0,
  "total_cost_bdt": 0,
  "peak_grid_kwh": 0,
  "plan_summary": "..."
}
```

**Validation enforced today:**

- `scenario_id` — non-empty string
- `operator_notes` — 1 to 3 non-empty strings
- `hours` — exactly 24 entries covering hours 0–23, no duplicates
- `battery` — all fields non-negative; `minimum_energy_kwh ≤ initial_energy_kwh ≤ capacity_kwh`

Malformed or invalid requests return `422` with a detailed field-level error body.

## Getting Started

**Prerequisites:** Python 3.11+

```bash
cd backend
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env   # fill in LLM provider values once the LLM layer is implemented
```

**Run the server:**

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**Test it:**

```bash
curl http://127.0.0.1:8000/health

curl -X POST http://127.0.0.1:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d @path/to/sample_request.json
```

Interactive API docs are available at `http://127.0.0.1:8000/docs` while the server is running.

## Environment Variables

| Variable | Purpose |
|---|---|
| `LLM_PROVIDER` | LLM provider identifier (used once the interpreter is implemented) |
| `LLM_MODEL` | Model name/identifier |
| `LLM_API_KEY` | Provider API key — **never commit this** |
| `PORT` | Service port (default `8000`) |

## Tech Stack

- **Backend:** Python 3.11, FastAPI, Pydantic
- **LLM:** provider/model TBD (interpretation layer not yet implemented)
- **Optimizer:** TBD

## Roadmap

1. LLM operator-note interpreter
2. Deterministic guardrail validation
3. 24-hour cost-minimizing optimizer
4. Final schedule verifier / replay check
5. Dockerize + deploy public endpoint
6. Full README with deployment instructions, model/provider disclosure, and sample walkthroughs
