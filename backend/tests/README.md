# API and public sample tests

Run from `backend` with the project's virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

Every public case is sent to `POST /optimize-energy`. Normal tests replay
fixture provider responses through the real interpreter and run the real
guardrails, solver, and verifier. The tests independently replay physics and
directives, recalculate cost and totals, and compare the objective with the
public optimal cost. They never compare or supply a reference hourly schedule.
Each case also runs with reordered forecasts and scaled energy/price values.

Actual LLM interpretation requires live providers and is tested separately:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_public_samples.py -k live_llm --live-llm
```

Live tests read `backend/.env` without printing credentials. Process environment
variables take precedence. These tests call configured external providers and
may consume quota. They fail when directive semantics differ, including when
providers fail and the interpreter falls back to `no_op`. A skipped live test
does not establish LLM accuracy.
