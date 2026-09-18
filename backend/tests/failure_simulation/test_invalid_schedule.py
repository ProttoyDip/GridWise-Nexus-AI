"""Scenario 4: optimizer returns a physics-violating schedule -> 500 from verifier.

We monkeypatch ``app.optimizer.solver.solve_energy_schedule`` to
produce a schedule whose end-of-day battery energy does not match
``battery.initial_energy_kwh`` (off by 1.0 kWh). The independent
verifier ``app.verifier.schedule_checker.verify_schedule`` must
detect the violation and the FastAPI route must convert it to
HTTP 500 (per ``app/api/optimize.py``: a ``ValueError`` from
``_verified_response`` is re-raised as HTTP 500).

We assert:
- response status is 500,
- response body contains the verifier's error detail,
- the patched solver was actually called.
"""

from __future__ import annotations

from app.optimizer import scheduler as scheduler_module
from app.verifier.schedule_checker import recalculate_totals

from tests.failure_simulation.conftest import (
    PRIMARY_SPEC, build_scenario, no_op_json, register,
)


def _build_inconsistent_plan(scenario, directives):
    """Return a schedule whose end-of-day battery energy is off by 1.0 kWh.

    The verifier requires ``battery_energy_after_kwh[23] ==
    battery.initial_energy_kwh``; we deliberately set it to
    ``initial + 1`` to trip that check while still passing every
    hour-by-hour balance / bound check individually. The simplest
    way is to keep all hours at the initial level (no charge/discharge)
    *except* hour 23, where we add a 1.0 kWh phantom flow that the
    continuity rule will catch.
    """
    from app.models.response import HourlyPlanEntry

    plan: list[HourlyPlanEntry] = []
    initial = scenario.battery.initial_energy_kwh
    for entry in scenario.hours:
        if entry.hour == 23:
            # Phantom flow: grid 1, no solar/battery change in this hour,
            # so the next hour (none) would see +1.0 continuity error.
            plan.append(
                HourlyPlanEntry(
                    hour=entry.hour,
                    grid_kwh=entry.demand_kwh + 1.0,
                    solar_used_kwh=0.0,
                    battery_action="idle",
                    battery_kwh=0.0,
                    battery_energy_after_kwh=initial,  # wrong hour-end state
                )
            )
        else:
            plan.append(
                HourlyPlanEntry(
                    hour=entry.hour,
                    grid_kwh=entry.demand_kwh,
                    solar_used_kwh=0.0,
                    battery_action="idle",
                    battery_kwh=0.0,
                    battery_energy_after_kwh=initial,
                )
            )
    return plan


def test_optimizer_returns_invalid_schedule_is_rejected_by_verifier(
    client, full_wiring, monkeypatch
):
    # Guarantee a clean optimization cache for this test's key so the
    # route always reaches the patched solver rather than returning a
    # cached valid plan from a prior run.
    from app.optimizer.cache import optimization_cache

    optimization_cache.clear()

    register(full_wiring, PRIMARY_SPEC, [no_op_json()])

    calls: list[int] = []

    def fake_solve(hours, battery, directives):
        # Reconstruct the ScenarioRequest the route passes us so we
        # can use its battery/hours to build a plan. operator_notes
        # is a sentinel because the test only cares about the battery
        # schedule returned to the verifier.
        from app.models.request import ScenarioRequest

        scenario = ScenarioRequest(
            scenario_id="failure-sim",
            operator_notes=["placeholder"],
            hours=hours,
            battery=battery,
        )
        calls.append(1)
        return _build_inconsistent_plan(scenario, directives)

    monkeypatch.setattr(scheduler_module, "solve_energy_schedule", fake_solve)

    response = client.post(
        "/optimize-energy",
        json=build_scenario(["ok"]).model_dump(),
    )

    assert calls, "solver monkeypatch was not invoked"
    assert response.status_code == 500, (
        f"expected verifier failure to surface as 500, got {response.status_code}: {response.text}"
    )
    # The 500 detail should mention verification (per the route's
    # "Schedule verification failed" message) or end-of-day continuity.
    detail = response.json().get("detail", "")
    assert "verification" in detail.lower() or "continuity" in detail.lower() or "battery" in detail.lower(), (
        f"unexpected 500 detail: {detail!r}"
    )
