from app.models.request import ScenarioRequest
from app.models.response import DirectiveInterpretation
from app.replanning import replan_remaining_day


def scenario() -> ScenarioRequest:
    return ScenarioRequest.model_validate({
        "scenario_id": "midday",
        "operator_notes": ["Do not discharge in the evening"],
        "hours": [{"hour": hour, "demand_kwh": 20, "solar_kwh": 5 if 8 <= hour <= 16 else 0, "tariff_bdt_per_kwh": 10} for hour in range(24)],
        "battery": {"capacity_kwh": 40, "initial_energy_kwh": 20, "minimum_energy_kwh": 5, "max_charge_kwh_per_hour": 10, "max_discharge_kwh_per_hour": 10},
    })


def test_replan_starts_from_measured_energy_and_returns_future_only():
    directive = DirectiveInterpretation(
        note_index=0,
        applies=True,
        directive_type="no_discharge_window",
        structured_adjustment={"hours": [18, 19]},
        explanation="Protect evening battery use.",
    )
    result = replan_remaining_day(scenario(), [directive], 12, 13)
    assert result["current_hour"] == 12
    assert len(result["remaining_plan"]) == 12
    assert result["remaining_plan"][0]["hour"] == 12
    assert result["remaining_plan"][-1]["hour"] == 23
    assert all(entry["battery_action"] != "discharge" for entry in result["remaining_plan"] if entry["hour"] in (18, 19))
