import json

from fastapi.testclient import TestClient

from app.main import app
from app.models.response import DirectiveInterpretation


def payload(*, flexible: bool = False) -> dict:
    data = {
        "scenario_id": "workflow",
        "operator_notes": ["No special restriction"],
        "hours": [
            {"hour": hour, "demand_kwh": 20, "solar_kwh": 5, "tariff_bdt_per_kwh": 5 if hour < 6 else 10}
            for hour in range(24)
        ],
        "battery": {
            "capacity_kwh": 40,
            "initial_energy_kwh": 20,
            "minimum_energy_kwh": 5,
            "max_charge_kwh_per_hour": 10,
            "max_discharge_kwh_per_hour": 10,
        },
    }
    if flexible:
        data["flexible_loads"] = [{
            "name": "Pump",
            "energy_kwh": 10,
            "max_power_kwh_per_hour": 5,
            "earliest_hour": 0,
            "latest_hour": 5,
        }]
    return data


def noop() -> DirectiveInterpretation:
    return DirectiveInterpretation(
        note_index=0,
        applies=False,
        directive_type="no_op",
        structured_adjustment=None,
        explanation="No constraint required.",
    )


def test_analysis_outage_and_replan_endpoints(monkeypatch):
    from app.api import analysis

    monkeypatch.setattr(analysis, "interpret_operator_notes_with_adaptive_consensus", lambda *args: [noop()])
    client = TestClient(app)

    analyzed = client.post("/analyze-scenario", json=payload())
    assert analyzed.status_code == 200
    assert analyzed.json()["conflict"]["feasible"] is True

    outage = client.post("/resilience/outage", json={
        "scenario": payload(),
        "start_hour": 18,
        "end_hour": 20,
        "critical_load_fraction": 0.5,
    })
    assert outage.status_code == 200
    assert outage.json()["outage_duration_hours"] == 3

    replanned = client.post("/replan", json={
        "scenario": payload(),
        "directives": [noop().model_dump()],
        "current_hour": 12,
        "current_battery_energy_kwh": 18,
    })
    assert replanned.status_code == 200
    assert len(replanned.json()["remaining_plan"]) == 12


def test_stream_exposes_flexible_assignments_without_changing_public_contract(monkeypatch):
    from app.api import optimize

    monkeypatch.setattr(optimize, "interpret_operator_notes", lambda *args: [noop()])
    client = TestClient(app)
    request = payload(flexible=True)

    public = client.post("/optimize-energy", json=request)
    assert public.status_code == 200
    assert set(public.json()["hourly_plan"][0]) == {
        "hour", "grid_kwh", "solar_used_kwh", "battery_action", "battery_kwh", "battery_energy_after_kwh",
    }

    streamed = client.post("/optimize-energy/stream", json=request)
    events = [json.loads(line) for line in streamed.text.splitlines() if line]
    result = next(event["data"] for event in events if event.get("event") == "result")
    assert all("flexible_loads" in entry for entry in result["hourly_plan"])
    assert sum(entry["flexible_loads"].get("Pump", 0) for entry in result["hourly_plan"]) == 10
