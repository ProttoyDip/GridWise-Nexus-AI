from app.models.request import ScenarioRequest
from app.resilience import evaluate_outage


def make_scenario(demand: float = 10.0, solar: float = 0.0) -> ScenarioRequest:
    return ScenarioRequest.model_validate({
        "scenario_id": "outage",
        "operator_notes": ["Protect critical load"],
        "hours": [{"hour": hour, "demand_kwh": demand, "solar_kwh": solar, "tariff_bdt_per_kwh": 10} for hour in range(24)],
        "battery": {
            "capacity_kwh": 20,
            "initial_energy_kwh": 20,
            "minimum_energy_kwh": 5,
            "max_charge_kwh_per_hour": 10,
            "max_discharge_kwh_per_hour": 10,
        },
    })


def test_outage_reports_survival_and_shortfall():
    result = evaluate_outage(make_scenario(), 0, 3, 0.5)
    assert result["survival_hours"] == 3
    assert result["fully_served"] is False
    assert result["unserved_energy_kwh"] == 5
    assert result["hourly"][-1]["battery_energy_after_kwh"] == 5


def test_solar_can_serve_critical_load_without_battery():
    result = evaluate_outage(make_scenario(solar=8), 10, 12, 0.5)
    assert result["fully_served"] is True
    assert result["unserved_energy_kwh"] == 0
    assert result["minimum_battery_energy_kwh"] == 20
