# Failure Simulation Report

End-to-end failure scenarios for the GridWise ``POST /optimize-energy`` pipeline. Each scenario exercises the real FastAPI route with a scripted fake LLM and asserts the documented fail-safe contract.

- **Overall:** **PASS**
- **Total scenarios:** 7
- **Passed:** 7
- **Failed:** 0

| # | Scenario | Description | Status | Duration (s) | Notes |
|---|----------|-------------|--------|--------------|-------|
| 1 | `test_invalid_json_triggers_repair_or_fallback` | LLM returns invalid JSON -> repair or fallback | **PASS** | 0.06 |  |
| 2 | `test_primary_429_retries_then_succeeds` | Primary model gives 429 -> next model used | **PASS** | 0.06 |  |
| 3 | `test_all_models_unavailable_returns_safe_no_op` | All models unavailable -> controlled error | **PASS** | 0.08 |  |
| 4 | `test_no_provider_chain_at_all_is_safe_no_op` | All models unavailable -> no provider chain | **PASS** | 0.06 |  |
| 5 | `test_optimizer_returns_invalid_schedule_is_rejected_by_verifier` | Optimizer creates invalid schedule -> validator catches it | **PASS** | 0.01 |  |
| 6 | `test_unknown_directive_type_is_replaced_with_no_op` | Wrong directive type returned -> guardrail rejection | **PASS** | 0.05 |  |
| 7 | `test_numeric_out_of_bounds_directive_is_replaced_with_no_op` | Numeric out-of-bounds directive -> guardrail rejection | **PASS** | 0.06 |  |

## How to reproduce

```bash
cd backend
python -m tests.failure_simulation.harness
```

Each scenario is also runnable directly with ``pytest``:

```bash
cd backend
python -m pytest tests/failure_simulation -v
```
