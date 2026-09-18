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
| Solver performance and warm starts | ✅ Equivalent lossless LP by default; native CBC MIP hints available |
| Directive constraints in optimizer | ✅ All five active directive types implemented |
| Final schedule verifier | ✅ Implemented |
| Internal confidence tracking | ✅ Agreement metadata and accept/verify/escalate policy implemented |
| Interpretation cache | ✅ In-memory TTL/LRU cache with concurrent request deduplication |
| Optimization result cache | ✅ Verified hourly plans and cost cached with TTL and safe failure fallback |
| LLM evaluation and measured defaults | ✅ All 34 candidates reported; 32 benchmarked with available credentials |
| Prompt evaluation | ✅ Three versioned prompts, public-sample scoring and ranking report |
| Judge simulator | ✅ Independent API, interpretation, physics, cost and optimum checks |
| Unit, API, and public sample tests | ✅ 740 offline tests passed; 10 live sample tests passed in the earlier live run |
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

JSON handling first requests a native schema-constrained response through OpenAI-compatible `response_format` or Anthropic `output_config.format`. An explicit unsupported-format rejection falls back to text. Text parsing uses `JSONDecoder.raw_decode`, then a string-aware brace/bracket balancing scanner for prose-wrapped output. Multiple JSON values, duplicate keys, incomplete containers, and non-finite numbers are rejected. Parsing or directive-validation failures retry with a repair prompt containing the original scenario context, validation error, and failed output; retries remain bounded. The arbiter uses the same parsing and repair path. Native request formats follow the [OpenAI](https://developers.openai.com/api/docs/guides/structured-outputs) and [Anthropic](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) documentation.

Guardrails check allowed directive types, note-index order, literal boolean `applies` values, integer hours from 0–23 in unique ascending order, required adjustment fields, and numeric bounds. Scenario-aware physical validation requires `0 < solar factor <= 1`, `0 <= battery reserve <= battery capacity`, and a non-negative grid limit. All numeric values must be finite. It safely normalizes plain numeric strings and integral hour representations, then sorts and deduplicates hours. Impossible values, fractional/out-of-range hours, and ambiguous values become `no_op`; quantities are never clamped or invalid hours silently dropped. Physical checks run before confidence voting and again before optimization using the request's battery context. A `no_op` has `applies=false` and a null adjustment; other directives have `applies=true`. API fields remain unchanged.

Interpretations also carry private internal `confidence_score`, `agreement_count`, and `models_used` metadata. A single valid model result scores `0.6` and requests verification by the next distinct provider/model. A strict majority with at least two agreeing models is accepted, with confidence equal to the agreeing fraction of valid votes. Disagreements escalate through further configured models; unresolved disagreements fall back to `no_op`. If only one valid result is available, it remains marked for verification and passes deterministic guardrails. Retries do not count as extra votes. Confidence measures agreement evidence, rather than a calibrated probability of correctness. Metadata is excluded from API responses and OpenAPI, and every schedule still undergoes final verification.

Final guarded interpretations are cached in process memory under a SHA-256 hash of the operator note and canonical directive context: battery configuration, sorted forecast, public provider/model identities, operating date, and prompt/policy version. The cache defaults to a 300-second TTL and 1,024 entries, evicts least-recently-used entries, and shares one in-flight computation for identical concurrent requests. Cached values are deep copies with the caller's note index restored, including private confidence evidence. API keys, provider objects, and raw responses are not stored. Failure or unresolved-disagreement fallbacks are not cached. Set the TTL to `0` to disable caching; each server process has its own cache. Final verification still runs on every API request.

Verified optimization results have a separate process-local cache in `app/optimizer/cache.py`. Its SHA-256 key includes the scenario ID, canonical sorted forecast, battery configuration, and all public validated directive fields. Entries store a deep copy of the hourly plan, total cost, and UTC insertion timestamp, with a 300-second TTL and a 1,024-entry LRU limit by default. Identical requests reuse the schedule without rerunning the solver; totals are recalculated and the cached cost and schedule are independently verified before responding. Expired or corrupt results trigger a fresh solve. Cache lookup, key-generation, storage, or configuration failures never prevent normal optimization, and unsuccessful/unverified schedules are not cached. TTL `0` disables this cache. Cache metadata is internal and does not change API fields.

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

The default solver uses an equivalent linear program instead of 24 binary action variables. Any simultaneous charge/discharge is cancelled by the smaller amount: net battery energy, energy balance, solar/grid use, and cost are preserved, and both rate usages decrease. This preserves every current directive, so the returned exclusive-action schedule has the same minimum cost as the original mixed-integer model. This argument depends on the current lossless battery and objective; efficiency losses or action-dependent costs would require revisiting the formulation.

Native CBC warm starts remain available through `solve_energy_schedule(..., use_binary_modes=True)`. A thread-safe history stores up to 32 independently verified solutions for 300 seconds. It matches normalized tariff profiles, capacity and normalized battery state/rates, and directive types/hours/numeric constraints within a 10% similarity threshold. Matching patterns are adapted to current capacity, initial energy, demand, and solar, then independently verified before all continuous and binary initial values are supplied to CBC. Initial values are hints, never fixed decisions. Unsupported starts, corrupt history, infeasible candidates, or failed warm solves fall back to a normal solve. Both solver status and solution status must establish optimality, with zero relative/absolute gaps and no time or node limits. Use `use_warm_start=False` to disable history reuse/storage. Unique temporary files prevent concurrent CBC warm solves from colliding and are cleaned after each attempt.

The [optimizer benchmark](backend/reports/optimizer_benchmark.md) compares direct solver calls without API result or interpretation caching: 10 public cases with 1% forecast, tariff, battery, and numeric-limit changes, repeated 10 times in alternating order. Average solve time was **53.831 ms before** (original cold binary model) and **41.472 ms after** (equivalent LP), a **22.96% improvement**. Every schedule was independently verified and all three formulations' objectives matched. Native warm binary solves averaged **59.580 ms**, so warm starts are optional rather than the default performance strategy for these small models.

Reproduce the benchmark from `backend`:

```bash
python -m scripts.benchmark_optimizer --repeats 10
```

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

Optimizer performance validation: **227 passed, 10 live tests skipped** across warm starts, the LP/binary equivalence, all public API scenarios, solver constraints, physical guardrails, cache behavior, and schedule verification. The latest full offline suite also passes the LLM racer tests.

```bash
python -m pytest -q -p no:cacheprovider
```

Latest offline result: **740 passed, 10 live tests skipped**. This includes independent judge checks and fault injection, prompt scoring and recommendation, optimization cache hits, expiry, invalidation and failures, physical validation and safe correction, evaluation scoring and measured default selection, interpretation cache, JSON parsing and repair, confidence policy and API privacy checks, plus 30 public sample API checks: each of the 10 cases runs with original inputs, reordered forecasts, and scaled energy/price values. Tests compare directive semantics, independently replay schedule constraints, recalculate totals, and compare cost with the public optimal objective. Reference hourly schedules are never hardcoded or supplied to the solver.

To test actual note interpretation using configured providers and credentials in `.env`:

```bash
python -m pytest -q -p no:cacheprovider tests/test_public_samples.py -k live_llm --live-llm
```

Latest live result: **all 10 public cases passed**, including directive interpretation, schedule validity, and optimal cost. Live runs call external providers and use their quota; offline replay tests do not establish LLM accuracy. See [tests/README.md](backend/tests/README.md) for details.

## LLM evaluation

Run every registered candidate directly against the public operator notes, using credentials from `backend/.env`:

```bash
cd backend
python -m scripts.evaluate_llm --workers 8 --timeout 12 --apply-defaults
```

Use `--samples PATH` for another fixture with the same `cases`, `input`, and `expected_output.directive_interpretation` structure; `--output PATH` sets the JSON report location. The script also writes a Markdown ranking. Omitting `--apply-defaults` only generates reports.

The [ranking report](backend/reports/llm_ranking.md) measures strict JSON validity, directive type, exact hours, numeric values (absolute/relative tolerance `1e-6`), and successful-response latency. Each candidate runs without cache, consensus, retries, repair, or model failover. Native structured output is attempted first; explicit unsupported-format errors permit text fallback. Failed calls count against accuracy, and missing credentials are reported separately. Hours and numeric rates exclude notes where those fields do not apply. Reports store error classes and scores, without credentials or raw model responses.

The completed run covered 10 cases and 18 notes per model: 32 candidates were called, while OpenAI and Anthropic lacked configured credentials. Four models reached 100% accuracy across all requested correctness metrics. ExperimentalLab `gpt-5.6-luna` ranked first with a median response time of about 2.0 seconds. It is the measured primary and fast model; OpenRouter `deepseek/deepseek-v4-flash-0731:free` is the arbiter from another provider. Provider errors, mainly quota limits, reduced other candidates' end-to-end scores. These results describe this public dataset and provider availability during this run; they do not establish hidden-test accuracy.

`--apply-defaults` writes [default_models.json](backend/app/llm/default_models.json). Selection requires complete coverage and at least 90% full semantic and strict JSON accuracy, then ranks accuracy before latency. Runtime role selectors and per-provider defaults read this file; the API tries the measured primary and eligible alternatives before the remaining fallback chain. Explicit model pins/lists retain their ordering. Missing or invalid selection files retain heuristic defaults. The full registry remains available for resilience, and API response fields remain unchanged.

## Prompt evaluation

The [versioned prompt directory](backend/evaluation/prompts) stores `prompt_v1.txt` (a frozen baseline), `prompt_v2.txt` (concise extraction rules), and `prompt_v3.txt` (a staged relevance/time/quantity/physics checklist). Evaluate all versions against the same public samples and fixed model:

```bash
cd backend
python -m evaluation.evaluate_prompts --repeats 2 --workers 3 --text-only
```

The evaluator selects the configured measured primary by default. Use `--provider NAME --model ID` to fix another model, `--samples PATH` for another labeled fixture, or `--output PATH` for another report location. Default output is [prompt_score_report.json](backend/evaluation/prompt_score_report.json). Use one worker for sequential latency measurement. Omitting `--text-only` attempts native structured output; the report records this because enforced JSON formatting can mask prompt differences.

Each version receives the same user context and provider/model, with no cache, guardrail correction, repair, retries, consensus, or failover. The report includes strict JSON validity, directive type and full directive accuracy, exact applicable hours, applicable numeric accuracy (absolute/relative `1e-6` tolerance), mean/median successful-response latency, error counts, and per-note/per-repetition scores. Provider errors count as failures, and reports never store credentials or raw responses. Dataset and prompt hashes identify the evaluated snapshots.

Recommendations require complete coverage and at least 90% full accuracy and JSON validity. Ranking prioritizes full directive accuracy, strict JSON validity, valid directive rate, then median latency. Accuracy ties are explicitly reported; a latency-based choice is provisional. No recommendation is emitted for partial or unsuccessful evaluations. Evaluations recommend a version without changing the production prompt or API response schema.

The completed text-only run used ExperimentalLab `gpt-5.6-luna`, 10 cases, 18 notes, and two repetitions per version (108 evaluations total). `prompt_v1` and `prompt_v2` each scored 97.2% full accuracy and strict JSON validity, with one quota error each; `prompt_v3` scored 94.4%, with two quota errors. All successfully returned interpretations were correct. The report provisionally recommends **`prompt_v1`**, whose median successful-response latency was 2.531 seconds versus `prompt_v2`'s 2.665 seconds. Quota failures and concurrent timing limit conclusions about intrinsic prompt quality; `prompt_v2` remains tied on full accuracy.

## Judge simulator

[judge_simulator.py](backend/judge_simulator.py) tests `/health`, OpenAPI endpoint declarations, malformed-request rejection, exact response fields, directive types/hours/numbers, and every hour's energy balance, battery transitions/bounds/rates, solar availability, expected directive compliance, recalculated totals, and end-of-day equality. Public objectives come from the sample pack; random objectives come from a separately built binary optimization model. Equivalent optimal schedules are accepted without comparing reference hourly schedules. Missing or malformed responses fail checks rather than aborting the evaluation.

```bash
cd backend
python judge_simulator.py --mode live --random-cases 12 --providers experimentallab
python judge_simulator.py --mode replay --random-cases 24 --output reports/judge_replay_estimate.json
```

Live mode exercises the actual API interpretation path and configured LLMs. `--providers` explicitly selects a local provider profile and records that override; omitting it preserves the configured chain. `--base-url http://127.0.0.1:8000` tests a running service using real HTTP. Replay mode supplies expected LLM JSON through a fake provider while still running the interpreter, guardrails, solver and API verifier; it tests plumbing and cannot establish real interpretation accuracy.

Random cases are reproducible using `--seed`, cover all six directive types, include percentage conversions and future/unrelated distractors, mix up to three notes, vary battery/forecast/tariff values (including zeros), and reorder some forecasts. They are constructed with a feasible idle-battery witness, then independently optimized. Requests and generated semantic labels are stored for reproduction. Reports contain per-category scores and failures, latency, actual/reference costs, public/random pass counts, weights, mode and profile, with no raw provider responses or credentials.

The default [score estimate report](backend/reports/judge_score_estimate.json) uses explicitly defined 100-point simulator weights. No official rubric or hidden cases were supplied, so this is an estimate rather than an official score. The completed live run passed all **10 public and 12 random cases** using the explicitly recorded ExperimentalLab profile, for an estimated **100/100**. The separate [replay report](backend/reports/judge_replay_estimate.json) passed all 10 public and 24 random cases for 100/100 in replay mode. A default-profile live attempt was stopped after OpenRouter exhausted its daily quota; that incomplete default-profile run is not included in the score. HTTP `--timeout` applies to remote requests; local ASGI calls use the application's own provider timeouts.

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
| `INTERPRETATION_CACHE_TTL_SECONDS` | Cache TTL in seconds; default `300`, or `0` to disable |
| `INTERPRETATION_CACHE_MAX_ENTRIES` | Maximum cached interpretations per process; default `1024` |
| `OPTIMIZATION_CACHE_TTL_SECONDS` | Result cache TTL in seconds; default `300`, or `0` to disable |
| `OPTIMIZATION_CACHE_MAX_ENTRIES` | Maximum optimized results per process; default `1024` |
| `PORT` | Example configuration value; pass the desired port explicitly with Uvicorn's `--port` |

`<NAME>` is the uppercase provider name. When no model override is set, the provider's built-in model list is used. Check `GET /llm/status` to inspect the resolved chain without exposing credentials.

## Internal decision tools and demo

- `app/explainability/generator.py` describes battery actions, applied constraints, and the cost difference from a labelled grid/solar-only reference. It never changes the schedule. The reference can violate operator constraints; its difference is not a guaranteed feasible saving.
- `app/simulation/scenario_generator.py` generates bounded hypothetical solar-loss, demand-increase, and battery-power futures. These are explicit stress-test assumptions, without assigned probabilities. The simulator independently optimizes and verifies each future, reports infeasibility, and compares cost, grid imports, battery throughput, and minimum energy. Its internal best plan is the nominal optimum; contingency plans depend on knowing the future, rather than forming a robust shared policy.
- Set `GRIDWISE_DIGITAL_TWIN_ENABLED=1` to generate futures before nominal optimization and retain comparisons internally after verification. Identical futures reuse the existing TTL optimization cache. Optional enrichment failures cannot fail a verified API response.
- `app/memory/store.py` stores exact normalized phrases, directive types, and successful/failed feedback counts using bounded atomic JSON persistence. `app/memory/directive_memory.json` is the empty seed format. Runtime storage defaults to the system temporary directory; set `GRIDWISE_MEMORY_PATH` on a persistent volume to retain feedback across restarts. Verified requests provide physical/structural validation evidence, not independent proof of correct intent. Corrective feedback can be recorded with `DirectiveMemory.record(..., success=False)`.
- Memory supplies advisory prompt context. At least two successful observations with an 80% validation success rate can increase an agreeing interpretation's private evidence score by 0.05. It never changes model agreement counts, accept/verify/escalate decisions, directive values, or schedules. `DirectiveMemory.graph()` relates phrases to directives and optimization effects internally.
- `app/agents/coordinator.py` coordinates virtual Energy Manager, Safety, Optimization, and Explanation roles without extra model calls. Explanations and optional twin results are held in a bounded internal decision store, accessible in Python using server-generated request IDs, never added to the `/optimize-energy` response.

Enable the isolated demo with `GRIDWISE_ENABLE_DEMO=1`, start the API, then open `/demo`. Its emergency button compares six fixed hypothetical futures without LLM calls or access to stored operator phrases. The dashboard displays measured public-sample model accuracy/latency from a packaged report and current runtime counters. Model evaluation automatically refreshes the packaged report after all candidates finish. To refresh it manually from an existing report:

```powershell
cd backend
.\.venv\Scripts\python.exe scripts/build_demo_benchmarks.py
```

Demo routes are disabled by default and excluded from OpenAPI. Competition request and response schemas remain unchanged. Runtime memory and internal decisions are not shared across workers or replicas.

## Tech Stack

- **Backend:** Python 3.11+, FastAPI, Pydantic
- **LLM:** Configurable provider/model chain using HTTPX, JSON parsing, retries, and quota failover
- **Optimizer:** PuLP 3.3.0 with bundled CBC solver
- **Verification:** Independent deterministic schedule replay
- **Tests:** pytest and FastAPI TestClient, with optional live LLM checks

## Roadmap

1. Deploy and verify the public endpoint using [DEPLOYMENT.md](DEPLOYMENT.md). Docker packaging and a Render Blueprint are provided; hosting publication and Linux image verification remain pending.
2. Add deployment-specific configuration and a reproducible sample walkthrough.
