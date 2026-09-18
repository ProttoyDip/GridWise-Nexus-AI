# GridWise Nexus AI

**An LLM-assisted campus energy optimization agent** — built for the BUP CSE Fest 2026 Hackathon (Online Preliminary), in association with Poridhi.

GridWise interprets natural-language operator notes (e.g. *"Solar output will drop to about 20% from 1 PM to 3 PM"*), converts them into structured, machine-checkable directives, validates them deterministically, and produces a cost-minimizing 24-hour campus energy schedule that respects every applicable constraint.

---

## Status

The complete optimization pipeline is implemented: `POST /optimize-energy` interprets operator notes, validates the directives, solves a 24-hour energy schedule with PuLP/CBC, and independently verifies the schedule before returning it. All 10 public sample cases passed with live LLM providers. Docker packaging and deployment remain pending.

| Component | Status |
| --- | --- |
| `GET /health` | ✅ Implemented |
| `POST /optimize-energy` — request/response schema & validation | ✅ Implemented |
| `GET /llm/status` — configured providers and models | ✅ Implemented |
| `POST /optimize-energy` — complete pipeline | ✅ Implemented |
| LLM operator-note interpreter | ✅ Implemented |
| Deterministic guardrails | ✅ Implemented |
| Energy schedule optimizer | ✅ PuLP/CBC solver implemented |
| Directive constraints in optimizer | ✅ All five active directive types implemented |
| Final schedule verifier | ✅ Implemented |
| Unit, API, and public sample tests | ✅ 236 offline tests and 10 live sample tests passed |
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
│   │   │   ├── llm_status.py  # GET /llm/status
│   │   │   └── optimize.py    # POST /optimize-energy (complete pipeline)
│   │   ├── models/
│   │   │   ├── request.py     # Request schema + validation (Pydantic)
│   │   │   └── response.py    # Response schema (Pydantic)
│   │   ├── llm/
│   │   │   ├── provider.py    # Provider/model chain and quota failover
│   │   │   ├── prompts.py     # Operator-note prompt templates
│   │   │   └── interpreter.py # JSON parsing, retries, and safe fallback
│   │   ├── guardrails/
│   │   │   └── validator.py   # Deterministic directive validation
│   │   ├── optimizer/
│   │   │   ├── scheduler.py   # Scenario scheduling entrypoint
│   │   │   ├── directives.py  # apply_directives() adds hourly constraints
│   │   │   └── solver.py      # PuLP/CBC cost minimization
│   │   └── verifier/
│   │       └── schedule_checker.py # Independent replay and totals checks
│   ├── tests/                 # Unit/API tests and public sample fixtures
│   ├── requirements.txt
│   └── .env.example
└── README.md
```

## Implemented Behavior

The interpreter processes one note at a time, assigns note indices deterministically, retries malformed responses and transient provider failures, and moves through the provider/model chain on quota exhaustion. Unavailable providers or invalid directives fall back to `no_op`, with the reason recorded in the explanation.

Guardrails check allowed directive types, note-index order, literal boolean `applies` values, integer hours from 0–23 in unique ascending order, required adjustment fields, and numeric bounds. Solar factors must be in `[0, 1]`; reserve and grid-cap values must be finite and non-negative. A `no_op` has `applies=false` and a null adjustment; other directives have `applies=true`.

| Directive | Optimizer behavior during selected hours |
| --- | --- |
| `solar_reduction` | Caps solar use at forecast solar × factor |
| `minimum_battery_reserve` | Raises the end-of-hour battery energy floor |
| `no_charge_window` | Sets battery charging to zero |
| `no_discharge_window` | Sets battery discharging to zero |
| `max_grid_window` | Caps hourly grid import at `max_grid_kwh` |
| `no_op` | Adds no constraints |

Overlapping solar and grid caps enforce the smallest limit; overlapping reserves enforce the largest floor, including the base battery minimum. Solar factors apply to the original forecast. Contradictory directives remain hard constraints and can make the schedule infeasible.

The solver minimizes `sum(grid_kwh × tariff_bdt_per_kwh)` with non-negative grid import, forecast-limited solar use, hourly energy balance, battery energy continuity, capacity and minimum-energy bounds, and charge/discharge rate limits. Charging and discharging cannot occur simultaneously. Hour 23 ends at the initial battery energy. Battery operation is lossless, grid export is forbidden, and excess solar may be curtailed.

The final verifier independently checks all 24 hours, energy balance, solar availability, battery transitions and limits, directive compliance, end-of-day equality, and recalculated grid energy, cost, and peak import. Comparisons allow absolute `1e-5` and relative `1e-7` tolerances for solver precision. Invalid schedules are rejected before a success response is returned.

## API Reference

### `GET /health`

Readiness probe. Returns `200 OK` once the service is ready.

```json
{ "status": "ok" }
```

### `GET /llm/status`

Additive, judge-schema-safe endpoint reporting which LLM provider(s)/model(s) are currently configured (never returns API keys):

```json
{
  "configured": true,
  "primary": "openrouter",
  "providers": [
    { "provider": "openrouter", "model": "openai/gpt-4o-mini" },
    { "provider": "nararouter", "model": "qwen3.8-27b" }
  ]
}
```

### `POST /optimize-energy`

Accepts a 24-hour energy scenario plus 1–3 operator notes; returns the interpreted directives and the optimized 24-hour schedule.

**Request** (abridged; comments are illustrative — send a complete JSON request with 24 hour entries):

```jsonc
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

**Response** (illustrative, abridged — totals are calculated from the actual schedule; see `app/models/response.py`):

```jsonc
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
  "total_grid_kwh": 0, // Actual sum of hourly grid import
  "total_cost_bdt": 0, // Actual sum of hourly grid import × tariff
  "peak_grid_kwh": 0, // Actual maximum hourly grid import
  "plan_summary": "..."
}
```

**Validation enforced today:**

- `scenario_id` — non-empty string
- `operator_notes` — 1 to 3 non-empty strings
- `hours` — exactly 24 entries covering hours 0–23, no duplicates
- `hours` values — non-negative demand, solar forecast, and tariff
- `battery` — positive capacity, non-negative remaining fields; `minimum_energy_kwh ≤ initial_energy_kwh ≤ capacity_kwh`

Malformed or invalid requests return `422` with a detailed field-level error body.

Optimization failures, including infeasible directive combinations, return `422`. Schedule verification failures return `500` with an error detail instead of an hourly plan. `GET /health` checks service availability; it does not establish LLM provider availability.

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
```

Copy `.env.example` to `.env` (`Copy-Item .env.example .env` in PowerShell, or `cp .env.example .env` on macOS/Linux), then configure provider credentials. The server command below loads that file. Without a usable provider, operator notes fall back to `no_op` and scheduling still uses the base scenario constraints.

**Run the server:**

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --env-file .env
```

**Test it:**

```bash
curl http://127.0.0.1:8000/health

curl -X POST http://127.0.0.1:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d @path/to/sample_request.json
```

Interactive API docs are available at `http://127.0.0.1:8000/docs` while the server is running.

## Tests

Run from `backend` with the virtual environment activated:

```bash
python -m pytest -q -p no:cacheprovider
```

Latest offline result: **236 passed, 10 live tests skipped**. This includes 30 public sample API checks: each of the 10 cases runs with original inputs, reordered forecasts, and scaled energy/price values. Tests compare directive semantics, independently replay schedule constraints, recalculate totals, and compare cost with the public optimal objective. Reference hourly schedules are never hardcoded or supplied to the solver.

To test actual note interpretation using configured providers and credentials in `.env`:

```bash
python -m pytest -q -p no:cacheprovider tests/test_public_samples.py -k live_llm --live-llm
```

Latest live result: **all 10 public cases passed**, including directive interpretation, schedule validity, and optimal cost. Live runs call external providers and use their quota; offline replay tests do not establish LLM accuracy. See [tests/README.md](backend/tests/README.md) for details.

## Environment Variables

| Variable | Purpose |
|---|---|
| `LLM_PROVIDERS` | Comma-separated provider priority order; takes precedence over `LLM_PROVIDER` |
| `LLM_PROVIDER` | Single provider: `anthropic`, `openai`, `openrouter`, `nararouter`, or `experimentallab` |
| `LLM_API_KEY_<NAME>` | Provider-specific key, such as `LLM_API_KEY_OPENROUTER` |
| `LLM_MODELS_<NAME>` | Ordered comma-separated model list for a provider |
| `LLM_MODEL_<NAME>` | Pin a provider to one model when its model list is unset |
| `LLM_MODEL` | Single-provider model override when no provider-specific override is set |
| `LLM_API_KEY` | Single-provider fallback key — **never commit real keys** |
| `PORT` | Example configuration value; pass the desired port explicitly with Uvicorn's `--port` |

`<NAME>` is the uppercase provider name. When no model override is set, the provider's built-in model list is used. Check `GET /llm/status` to inspect the resolved chain without exposing credentials.

## Tech Stack

- **Backend:** Python 3.11+, FastAPI, Pydantic
- **LLM:** Configurable provider/model chain using HTTPX, JSON parsing, retries, and quota failover
- **Optimizer:** PuLP 3.3.0 with bundled CBC solver
- **Verification:** Independent deterministic schedule replay
- **Tests:** pytest and FastAPI TestClient, with optional live LLM checks

## Roadmap

1. Dockerize and deploy a public endpoint.
2. Add deployment-specific configuration and a reproducible sample walkthrough.
