# GridWise Deployment Readiness Checklist

Written for: whoever deploys this service next (teammate, CI/CD, or future you).

Status legend: ✅ verified working · ⚠️ works but needs attention before production · ❌ missing/blocking · ℹ️ informational

Every item below was actually checked against the current repo state — this is not a generic template. Where a claim needed proof, the command/output that proved it is noted. This is the second pass: every item flagged ❌/⚠️ in the first review has been fixed and re-verified (not just documented as a known issue).

---

## 1. Backend

### 1.1 Docker builds — ✅ fixed and verified with a real build+run

Added `Dockerfile` (repo root) and `.dockerignore`. **Actually built and ran the image** (not just written blind):

```
docker build -t gridwise-test -f Dockerfile .   → succeeded
docker run -d -p 18000:8000 gridwise-test        → container started cleanly
curl http://localhost:18000/health               → {"status":"ok"}
curl http://localhost:18000/system/status        → {"optimizer_status":{"available":true,"solver":"CBC"}, ...}
POST http://localhost:18000/optimize-energy      → 200, valid 24-hour schedule
```

This specifically resolves the previously-flagged risk that `pulp`'s bundled CBC solver binary might not work inside a Linux container — confirmed it does, via both the status probe and a full end-to-end optimization request. Base image is `python:3.12-slim`, addressing the Python-version-pin gap (1.4) at the same time. Test container/image were removed after verification (`docker stop/rm/rmi`).

### 1.2 Environment variables — ✅ documented and git-safe

Unchanged from the first pass: `backend/.env.example` documents every variable read by the app, `.env` is git-ignored and confirmed untracked. Added `CORS_ALLOW_ORIGINS` to the example file for the new CORS middleware (§1.3).

### 1.3 API endpoints — ✅ all verified live, gaps closed

| Method | Path | Verified | Notes |
|---|---|---|---|
| GET | `/` | ✅ 200 | **New** — trivial root route for platforms that health-check `/` by default |
| GET | `/health` | ✅ 200 | Readiness probe |
| POST | `/optimize-energy` | ✅ 200 (live, and in Docker) | Full pipeline |
| GET | `/llm/status` | ✅ 200 | No keys exposed |
| GET | `/system/status` | ✅ 200 | Health, model availability, optimizer status |

**CORS middleware added** (`app/main.py`) — configurable via `CORS_ALLOW_ORIGINS` (defaults to `*` for local/demo use). Verified with a real preflight `OPTIONS` request and a cross-origin `GET`, both returning the correct `access-control-allow-origin` header (`tests/test_main_app.py`).

### 1.4 Startup command — ✅ fixed

- README's documented `uvicorn app.main:app --host 0.0.0.0 --port 8000 --env-file .env` still works for local dev.
- Docker image's `CMD` intentionally omits `--reload` (dev-only, would break under WSGI/production supervisors) and `--env-file` (containers get config from the orchestrator's real environment, not a mounted file).
- Python version is now pinned via the Docker base image (`python:3.12-slim`) rather than left to whatever's on the dev machine (previously 3.14.3, newer than most platforms support).

⚠️ Still not fixed, by design — **multi-worker/multi-replica deployments have per-process state.** The interpretation cache, optimization cache, and circuit breaker (§2.3) all live in one process's memory. This is not a correctness bug (each process still behaves safely and independently), but if you scale horizontally, each replica warms up its own circuit-breaker/cache state independently. Acceptable for this project's scope; worth knowing if you later add Redis-backed shared state.

---

## 2. LLM layer

### 2.1 Fallback — ✅ verified

Unchanged from the first pass, still true: multi-provider/multi-model chain, verified by test and by live traffic cascading through real rate-limited models to a working one.

### 2.2 Timeout — ✅ verified, and the cascading-latency risk is now actually fixed

Previously flagged: **a single note could take 81–114 seconds** in the worst case (observed live), risking a gateway/reverse-proxy timeout cutting off a request the server was still legitimately processing.

**Fixed**: added `MAX_TOTAL_SECONDS_PER_NOTE = 25.0`, a hard wall-clock budget enforced in `app/llm/interpreter.py`'s chain walk. Once spent, the walk stops starting new provider attempts and falls through to the same safe verify/no_op fallback already used when providers are exhausted — never a partial or inconsistent result. Verified with a dedicated test (`test_per_note_deadline_stops_starting_new_models`) that sets the budget to 0 and confirms zero calls are made once expired.

This caps the worst case at a known, configurable number instead of "however long the fallback chain happens to take" — set it based on your actual gateway timeout.

### 2.3 Rate limits — ✅ verified, and the circuit breaker is now actually wired in

Previously flagged: the circuit breaker (`app/llm/circuit_breaker.py`) existed and was unit-tested but **only observed failures — it wasn't consulted when deciding which model to try**, so it had no actual effect on production traffic.

**Fixed**: `app/llm/interpreter.py` and `app/llm/consensus.py` now both (a) filter the provider chain through `circuit_breaker.filter_chain()` before walking it, skipping models with an open circuit (3+ recent 429/timeout/5xx failures, 60s cooldown) without even attempting a doomed request, and (b) call `circuit_breaker.record_success()` / `record_failure()` on every real attempt. Verified with new tests: a model with an open circuit is skipped entirely (`test_open_circuit_is_skipped_without_being_called` — asserts zero calls), and a success clears prior failures (`test_success_resets_the_circuit`).

Found and fixed a real bug while wiring this in: `filter_chain()` assumed every provider object has a `.model` attribute, which isn't true for several test fakes (and could be false for a minimal real provider) — every other call site in the codebase uses `getattr(provider, "model", name)` defensively; `filter_chain` now matches that convention.

Also found and fixed a test-isolation bug this surfaced: the circuit breaker is a process-wide singleton, so without a reset it leaked state between tests in the same pytest session. Added it to the existing `isolated_interpretation_cache` autouse fixture in `tests/conftest.py` (same pattern already used for the interpretation/optimization caches).

---

## 3. Optimizer

### 3.1 No crashes — ✅ unchanged, still verified

Four adversarial scenarios (all-zero demand/solar, pinned battery, extreme demand spike, frozen battery) all still handled without crashing — re-verified this pass, plus a fifth real-world check: **the solver now confirmed working inside the actual Docker/Linux deployment target**, not just this Windows dev machine.

### 3.2 Deterministic output — ✅ unchanged, still verified

5-run identical-hash proof from the first pass still holds; not re-run this pass since nothing touched the solver.

---

## 4. Security

### 4.1 No secrets committed — ✅ unchanged, still verified

Re-confirmed: `git grep` for known key patterns across tracked files — zero matches. `.env` still untracked.

### 4.2 No API keys exposed via the API — ✅ unchanged, still verified

### 4.3 Safe logs — ✅ unchanged, still verified

---

## Summary

| Area | First pass | This pass |
|---|---|---|
| Docker builds | ❌ no Dockerfile | ✅ built, run, and hit live — including inside the container |
| Environment variables | ✅ | ✅ (+ `CORS_ALLOW_ORIGINS` documented) |
| API endpoints | ✅ | ✅ (+ `/` root route, + CORS) |
| Startup command | ⚠️ no version pin | ✅ pinned via Docker base image |
| LLM fallback | ✅ | ✅ |
| LLM timeout | ⚠️ unbounded worst case (81-114s observed) | ✅ hard 25s per-note budget enforced and tested |
| LLM rate limits | ⚠️ breaker built but not wired in | ✅ wired into both interpreter and consensus chain walks, tested |
| Optimizer crashes | ✅ | ✅ (+ confirmed inside Docker) |
| Optimizer determinism | ✅ | ✅ |
| No secrets committed | ✅ | ✅ |
| No API keys exposed | ✅ | ✅ |
| Safe logs | ✅ | ✅ |

**Bugs found and fixed while doing this pass** (not pre-planned, discovered during the actual fix-and-verify work):
1. `circuit_breaker.filter_chain()` crashed (`AttributeError`) on any provider without a `.model` attribute — now uses the same defensive `getattr` pattern as every other call site.
2. Wiring the circuit breaker into production code paths exposed a pre-existing test-isolation gap (a process-wide singleton with no reset between tests) — fixed via the existing shared-state-reset fixture convention.
3. A long-standing, previously-dismissed-as-"environmental" test failure (`PermissionError` on `%TEMP%\pytest-of-user`, affecting ~22 tests across every session this repo has seen) was actually diagnosed this pass: the directory has a broken ACL (even `Get-Acl` on it raises `UnauthorizedAccessException` for the owning user). Fixed properly via `backend/pytest.ini` pointing `--basetemp` at a project-local, always-writable directory — not worked around, not re-excluded from the suite. **All 746 tests now pass with zero errors.**

No remaining blockers. The one structural note carried forward: per-process state (caches, circuit breaker) means horizontal scaling gets independent state per replica — acceptable for this project's current scope, worth revisiting if that changes.

---

## Frontend

**There is no frontend in this repository.** Confirmed by exhaustive search: no `package.json`, no `.html` files, no `frontend`/`client`/`web`/`ui`/`src` directory, no framework config (Vite/Next/Angular/etc.) anywhere in the repo tree. This is a backend-only FastAPI service.

Consequently: there is nothing to check for "frontend compatibility," and no user can currently exercise GridWise's features through a browser UI — the only way to use this service today is direct HTTP calls to the API (`curl`, Postman, the auto-generated Swagger UI at `/docs`, or a script), or a frontend that doesn't exist yet in this codebase.

If you want people to be able to try this without hand-writing HTTP requests, the practical options are: (a) rely on the built-in Swagger UI at `/docs` (already works today, zero extra effort — it's interactive and lets anyone submit real requests against the live schema), or (b) have a minimal purpose-built page built (a single HTML/JS page that POSTs to `/optimize-energy` and renders the schedule/directives). Say which you want and I'll do it.
