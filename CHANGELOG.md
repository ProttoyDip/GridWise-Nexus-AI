# Changelog

All notable changes to GridWise Nexus AI will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

Snapshot of the codebase as of **pre-changelog-checkpoint** (`fc381c4`),
branched from `NewFeature`. Documents the currently implemented
architecture, what already works end-to-end, and what is staged but not
yet wired in.

### Added — Demo UI overhaul (control-room dashboard)

`GET /demo` (opt-in via `GRIDWISE_ENABLE_DEMO=1`) has been rebuilt from a
plain JSON-output developer page into a control-room dashboard. The
backend now also exposes a real end-to-end optimization endpoint so the
UI can render an honest before/after delta driven by the live
`/optimize-energy` pipeline.

- **`POST /demo/run-optimization`** (`app/demo/routes.py`). Accepts
  `{"operator_notes": [str, ...]}` (1–3 notes), then runs the operator's
  scenario and a parallel baseline scenario (no constraints) through
  `optimize_energy`, returning both plans plus a `savings` block
  (`bdt`, `pct`, `grid_kwh`). Failures in either run surface as
  `HTTP 500` with a descriptive detail.
- **`_directive_card()` helper**. Projects each `DirectiveInterpretation`
  into a UI-shaped dict with icon (☀ / 🔋 / ⛔ / ⚡ / •), human-readable
  title, hour-window and reduction/cap summary lines, confidence score,
  and the explanation string.
- **`backend/app/demo/index.html` rewritten** (~43 KB, single file, no
  build step). Adopts a dark-navy / electric-blue / green glassmorphism
  theme and replaces the old JSON output with:
  - Sticky header with brand mark and three live status pills
    (LLM / Optimizer / Grid Simulation), fed by `GET /system/status`.
  - **Dashboard / Simulation / AI Insights** tab strip.
  - **Operator Console** card: textarea + three example chips
    (Solar Maintenance, Battery Protection, Peak Demand Control) that
    pre-fill the field on click.
  - **AI Processing Timeline** with 5 animated stages
    (Understanding → Detecting → Validating → Running → Generating).
  - **Directive Cards** grid with icon, hour window, summary, and a
    gradient confidence bar.
  - **Optimization Summary** with three highlighted savings cells
    (Before / After / Savings) and a delta vs baseline.
  - **Before vs After AI** comparison card with animated gradient
    bars for grid usage and cost.
  - **Interactive Energy Charts** powered by Chart.js 4.4.1 — battery
    reserve % (line) and grid demand (bar) over 24 hours.
  - **AI Agents** side panel (Interpretation / Safety / Optimization /
    Explanation) with live "Working / Passed / Completed / Ready"
    badges that flip through the timeline.
  - **Why this strategy?** rationale card that derives 6–7 verification
    reasons from the actual plan (charge / discharge totals, peak hour,
    reserve floor, applied directives, re-verification status).
  - **Loading overlay** with progressive agent steps.
  - Mobile-responsive collapse points at 980px, 760px, and 700px so the
    two-column grid, comparison arrow, and savings cells reflow cleanly.
- The **Simulation** tab keeps the original "Simulate Emergency"
  behaviour (`POST /demo/simulate-emergency`) and re-skins the results
  table.
- The **AI Insights** tab is populated after the first operator run with
  the directive list, plan summary, and verification status.

### Added — Risk-based adaptive verification

`POST /optimize-energy` now scales its LLM verification effort to the
per-note risk of the operator's instruction, instead of running every
note through the same verification path. The change is internal to the
interpret stage — request shape, response shape, confidence visibility,
and the existing fallback semantics are unchanged.

- **New module `app/llm/risk_classifier.py`**. A pure, deterministic
  classifier that assigns each operator note to one of three tiers
  before any model is called:
  - `LOW` — no asset keywords, no numeric tokens, no time-window cues.
    Single model call.
  - `MEDIUM` — asset keyword plus a numeric token, *or* multiple
    time-window cues. Two-model verification (primary + alternate
    provider). Arbiter only on disagreement.
  - `HIGH` — multiple numeric tokens plus an asset keyword, a wide
    hour window (≥ 6 hours), or multiple independent numeric
    constraints. Three models + arbiter on disagreement.
  A refinement step (`refine_risk_with_directive`) re-runs the
  classifier against the primary's structured output and *escalates*
  (never de-escalates); for example, a note that looks LOW by text but
  yields a numeric-bearing primary is promoted to MEDIUM.
- **New module `app/llm/adaptive_consensus.py`**. The router. It runs a
  text-only `LOW` gate first as a fast path, calls the primary, then
  hands off to one of three strategies based on the refined risk:
  - `_interpret_low_path` — primary only. Arbiter only on parse or
    validation failure (preserve existing single-call fallback).
  - `_interpret_medium_path` — primary + alternate-provider
    confirmation; arbiter only if they disagree on directive type or
    numeric value (beyond the existing tolerance tables).
  - `_interpret_high_path` — primary + secondary + a third model on a
    distinct provider; 2-of-3 majority via `assess_confidence` wins
    without an arbiter, otherwise arbiter resolves.
  - Single-provider safety: if only one provider is configured, all
    tiers collapse to a single primary call — preserves the
    `provider.calls == len(operator_notes)` invariant that
    `test_public_sample_pipeline` relies on.
  - Empty provider chain → safe `no_op` fallback (unchanged behavior).
- **Wiring change** in `app/api/optimize.py`: `interpret_operator_notes`
  is now bound to
  `app.llm.adaptive_consensus.interpret_operator_notes_with_adaptive_consensus`
  via a module-level alias, so existing monkeypatches of
  `app.api.optimize.interpret_operator_notes` continue to work without
  modification.
- **Tests** — `backend/tests/test_risk_classifier.py` (16 unit tests
  covering text classification, directive refinement, and the
  combined `classify_risk` entry point) and
  `backend/tests/test_adaptive_consensus.py` (10 integration tests
  exercising the three risk tiers with scripted providers, including
  agreement / disagreement branches, arbiter routing, three-way
  majority, and the single-provider collapse).
- **Not changed** — the API request/response schema, the directive
  whitelist, the guardrail validator, the optimizer, the final
  schedule verifier, the interpretation cache, and the
  confidence-storage path. Confidence metadata continues to live on
  `PrivateAttr` and is still excluded from API output and OpenAPI.

### Current architecture

Operator notes are natural-language and are never trusted as math. They
flow through a fixed pipeline before they can touch the optimizer, with
each stage producing strictly structured, locally-checked outputs the
next stage consumes.

```text
   POST /optimize-energy
            │
            ▼
   ┌────────────────────┐
   │  Adaptive Router   │  app.llm.adaptive_consensus
   │  (risk-tier aware) │   - text-only LOW gate
   └────────────────────┘   - LOW → 1 model
            │                - MEDIUM → primary + alternate
            │                - HIGH → primary + alt + 3rd + arbiter
            ▼                - single-provider → collapse to LOW
   ┌────────────────────┐
   │   Risk Classifier  │  app.llm.risk_classifier
   │   (pre-LLM, text)  │   - LOW / MEDIUM / HIGH from tokens
   └────────────────────┘   - refined against primary directive
            │
            ▼
   ┌────────────────────┐
   │   LLM Interpreter   │  app.llm.interpreter
   │  (untrusted out)   │   - structured-output first, text fallback
   └────────────────────┘   - failure-specific retry policy
            │                - concurrent per-note (asyncio.to_thread +
            │                  asyncio.gather, GIL-releasing sync I/O)
            ▼
   ┌────────────────────┐
   │ Guardrail Validator │  app.guardrails.validator
   │   (deterministic)  │   - directive type whitelist
   └────────────────────┘   - ascending-unique hours 0..23
            │                - finite, bounded numeric fields
            ▼                - failure → per-note no_op (never raises)
   ┌────────────────────┐
   │   Math Optimizer    │  app.optimizer
   │   (PuLP + CBC)      │   - scheduler.py → solver.py → directives.py
   └────────────────────┘   - cost-minimizing 24h LP
            │                - lossless battery, no grid export
            ▼
   ┌────────────────────┐
   │   Final Verifier    │  app.verifier.schedule_checker
   │  (independent)     │   - independent physics replay
   └────────────────────┘   - directive compliance re-check
            │                - totals (grid kWh, cost, peak) re-derived
            ▼
       OptimizeResponse
```

#### Component-by-component

| Component | Module(s) | Responsibility |
| --- | --- | --- |
| **LLM pipeline** | `app/llm/interpreter.py`, `app/llm/prompts.py`, `app/llm/json_parser.py`, `app/llm/output_schema.py` | One prompt → one `DirectiveInterpretation` per note. Native structured output is attempted first; an explicit unsupported-format reply falls back to text parsing (`JSONDecoder.raw_decode` + balanced-brace scanner). Malformed results are retried with a repair prompt containing the original scenario, the validation error, and the failed output. |
| **Router** | `app/llm/provider.py`, `app/llm/model_registry.py`, `app/llm/measured_defaults.py` | Builds an ordered `(provider, model)` chain from environment variables (`LLM_PROVIDERS`, `LLM_MODELS_<NAME>`, `LLM_API_KEY_<NAME>`). Concrete classes for `AnthropicProvider` and `OpenAICompatibleProvider`; one shared set of typed exceptions (`LLMQuotaExceededError`, `LLMTimeoutError`, `LLMServerError`, `StructuredOutputUnsupported`). `model_registry.py` adds a curated role layer (primary / fast / arbiter) and `measured_defaults.py` promotes measured `default_models.json` selections above heuristic priors while keeping the full chain for resilience. |
| **Consensus layer** | `app/llm/consensus.py`, `app/llm/confidence.py` | Spot-check consensus for numeric-bearing directives: primary → alternate-provider confirmation → arbiter on disagreement. `confidence.assess_confidence` records strict-majority accept / single-vote verify / unresolved escalate outcomes, attached to directives via `PrivateAttr` so they never appear in API output. |
| **Cache** | `app/llm/cache.py` | In-process TTL/LRU cache keyed by `SHA-256(note + canonical context)`. Context covers battery config, sorted forecast, public provider/model identities, today's date, and prompt + policy versions. Deep-copied returns; in-flight `concurrent.futures.Future` deduplication so identical concurrent requests share one computation. `INTERPRETATION_CACHE_TTL_SECONDS=0` disables caching. |
| **Guardrails** | `app/guardrails/validator.py` | Deterministic per-directive and per-batch checks. Allowed directive types: `solar_reduction`, `minimum_battery_reserve`, `no_charge_window`, `no_discharge_window`, `max_grid_window`, `no_op`. Hours must be integers 0..23, unique, ascending. `factor` is constrained to `[0, 1]`; reserve and grid-cap values must be finite, non-negative. Invalid entries are replaced with safe `no_op`; the layer never raises to the caller. |
| **Optimizer** | `app/optimizer/{solver,directives,scheduler}.py` | `scheduler.build_hourly_plan` → `solver.solve_energy_schedule` → PuLP `LpProblem` solved by bundled CBC. Decision variables: `grid[h]`, `solar_used[h]`, `battery_charge[h]`, `battery_discharge[h]`, `battery_energy[h]`, `charging[h]` (binary, mutually excludes charge and discharge). Objective: `min Σ grid[h] × tariff[h]`. Constraints cover energy balance per hour, battery continuity, charge/discharge rate limits, capacity and minimum-energy bounds, and `battery_energy[23] == initial_energy_kwh`. `apply_directives` translates every validated directive into a per-hour LP constraint: solar factors apply to the original forecast; overlapping solar/grid caps take the smallest bound, overlapping reserves take the largest floor. |
| **Validator** | `app/verifier/schedule_checker.py` | Independent replay. Re-checks every hour for: finite, non-negative energy values; energy balance; solar forecast bound; battery continuity, bounds, and rate limits; per-directive compliance; end-of-day equality with `initial_energy_kwh`. Totals (`total_grid_kwh`, `total_cost_bdt`, `peak_grid_kwh`) are re-derived from the schedule and compared to the reported fields. Tolerances: `abs_tol=1e-5`, `rel_tol=1e-7`. Verification failures raise `ScheduleValidationError` → surfaced as HTTP 500 from the route. |
| **API endpoints** | `app/main.py`, `app/api/{health,llm_status,optimize}.py`, `app/models/{request,response}.py` | FastAPI app exposes three routes: `GET /health` (readiness), `GET /llm/status` (credential-free provider/model visibility), `POST /optimize-energy` (full pipeline). Request and response models use Pydantic validators to enforce the judge schema in-process (24 hours covering 0..23 once; operator notes 1..3 non-empty; battery bound checks; response-level note-index ordering; peak matches plan). |

#### Request / response flow (current behavior)

`POST /optimize-energy` (`app/api/optimize.py`) is a sync route:

1. Validate the request via Pydantic (returns 422 on shape errors).
2. `interpret_operator_notes` runs every note concurrently through the
   measured-default-ordered provider chain with the configured cache.
3. `validate_directive_interpretation` re-validates the decoded output
   and substitutes safe `no_op` for any rejected entry.
4. `build_hourly_plan` solves the LP under those hard constraints.
5. `recalculate_totals` derives `total_grid_kwh`,
   `total_cost_bdt`, `peak_grid_kwh` from the resulting schedule.
6. The full response is assembled and `verify_schedule` replays it. Any
   verification failure raises → HTTP 500 with an error detail.
   Optimization failure (infeasibility, solver error) → HTTP 422.

### Current working features

The release-quality checklist follows the README status table, verified
against the code in this branch:

- **`GET /health`** — ready probe returning `{"status": "ok"}`.
- **`POST /optimize-energy`** — FastAPI route with Pydantic request
  validation, end-to-end pipeline (interpret → guardrail → solve →
  verify), and Pydantic response validation. Returns 422 on
  optimization failure; 500 on schedule verification failure.
- **`GET /llm/status`** — returns the resolved provider/model chain
  (never API keys). Falls back to `{configured: false, reason, providers:
  []}` on configuration error.
- **LLM operator-note interpreter** — native schema-constrained output
  (OpenAI `response_format` / Anthropic `output_config.format`) with
  deterministic text-fallback on explicit `StructuredOutputUnsupported`;
  `JSONDecoder.raw_decode` then balanced-brace string-aware fallback;
  multiple-JSON / duplicate-key / non-finite-number rejection; bounded
  repair-prompt retries on parse/validation failure.
- **Failure-specific retry policy** — `LLMQuotaExceededError` →
  exponential backoff on the same model; `LLMTimeoutError` → skip to
  next model immediately; `LLMServerError` (5xx) → short flat retry;
  parse/validation failure → immediate retry with the repair prompt
  (no sleep); anything else → flat linear backoff.
- **All five active directive types implemented**:
  `solar_reduction`, `minimum_battery_reserve`, `no_charge_window`,
  `no_discharge_window`, `max_grid_window`, plus `no_op`.
- **Deterministic guardrails** — directive-type whitelist, note-index
  order, literal `applies` semantics, ascending-unique integer hours
  in 0..23, finite non-negative numerics, `factor ∈ [0, 1]`.
- **Internal confidence tracking** — single valid model → `verify`
  (score 0.6); strict majority with ≥ 2 agreeing models → `accept`
  (score = agreeing / valid votes); unresolved disagreement →
  `escalate`. Stored on `PrivateAttr`, excluded from API responses and
  OpenAPI schema. Every schedule still undergoes final verification.
- **Interpretation cache** — in-process TTL/LRU keyed by SHA-256 of
  note + canonical context; concurrent-request deduplication via shared
  `Future`; `INTERPRETATION_CACHE_TTL_SECONDS=0` disables; failure
  and unresolved-disagreement fallbacks are not cached.
- **Final schedule verifier** — independent replay of physics, every
  directive, end-of-day battery equality, and totals
  (`total_grid_kwh`, `total_cost_bdt`, `peak_grid_kwh`).
- **Tests** — 575 offline tests passing (10 skipped) across providers,
  parser, consensus, confidence, cache, validator, optimizer directives,
  solver, schedule checker, models, public samples (reordered and scaled
  inputs), risk classifier, and adaptive consensus. Evaluator script
  `scripts/evaluate_llm.py` covers 32 of 34 candidates when credentials
  are present; live runs against all 10 public cases previously passed.

### Upcoming improvements

These are visible in the codebase but intentionally not wired into
`POST /optimize-energy` yet — they are staged so the next iteration can
turn them on without rework:

- **Circuit breaker for LLM calls** (`app/llm/circuit_breaker.py`).
  Per-`(provider, model)` cooldown breaker that counts only
  429 / timeout / 5xx failures (3 consecutive → 60s skip). The breaker
  is fully implemented and isolated; the next step is wiring it into
  `app.llm.interpreter._interpret_single_note` (filter the chain with
  `filter_chain` and `record_success` / `record_failure_*` around each
  HTTP call).
- **Consensus layer for live traffic** (`app/llm/consensus.py`). The
  primary → alternate-provider → arbiter flow is now wired into
  `POST /optimize-energy` indirectly through the risk-based adaptive
  router (`app/llm/adaptive_consensus.py`) — MEDIUM and HIGH notes invoke
  it automatically; LOW notes use a single primary call. A direct,
  always-on `interpret_operator_notes_with_consensus[_async]` mode is
  still available for callers that want to opt in without the risk
  classifier in front.
- **Surface breaker state via `GET /llm/status`**. `CircuitBreaker.snapshot()`
  exists for this purpose; exposing it as an additive, credential-free
  field is a natural fit alongside the existing provider list.
- **Calibrated scoring from production traffic**. `ModelSpec.json_reliability_score`,
  `reasoning_score`, and `expected_latency` in `model_registry.py` are
  explicitly heuristic priors, not measurements from the actual
  interpretation task — recalibrating them from benchmark and live data
  is on the roadmap.
- **Docker packaging and public deployment**. Listed in the README
  roadmap; the FastAPI entrypoint is already
  uvicorn-compatible (`uvicorn app.main:app`), so a slim Dockerfile +
  start command is the remaining work.
- **Reproducible sample walkthrough and deployment-specific
  configuration** (README roadmap item 2).
- **Pydantic `model_validator` consistency tightening**. The response
  validator currently re-derives `peak_grid_kwh` from
  `hourly_plan` (abs tolerance 0.01); tightening the response-side
  check against the verifier's `abs_tol=1e-5` would let the verifier
  drop one redundant comparison.

### Notes

- No code in this repository was modified to produce this entry.
- The safe development checkpoint for this entry is branch
  `pre-changelog-checkpoint` at `fc381c4` (parent: `NewFeature`).
- API response shape, request shape, and judge-visible fields are
  unchanged by anything in this Unreleased section.
