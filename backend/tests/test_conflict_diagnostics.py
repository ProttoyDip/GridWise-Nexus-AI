from app.diagnostics.conflicts import diagnose_conflicts
from app.models.request import ScenarioRequest
from app.models.response import DirectiveInterpretation


def scenario() -> ScenarioRequest:
    return ScenarioRequest.model_validate({
        "scenario_id": "conflict-test",
        "operator_notes": ["Limit grid", "Do not discharge"],
        "hours": [
            {
                "hour": hour,
                "demand_kwh": 60.0,
                "solar_kwh": 0.0,
                "tariff_bdt_per_kwh": 10.0,
            }
            for hour in range(24)
        ],
        "battery": {
            "capacity_kwh": 50.0,
            "initial_energy_kwh": 20.0,
            "minimum_energy_kwh": 0.0,
            "max_charge_kwh_per_hour": 20.0,
            "max_discharge_kwh_per_hour": 20.0,
        },
    })


def directive(note_index: int, kind: str, adjustment: dict) -> DirectiveInterpretation:
    return DirectiveInterpretation(
        note_index=note_index,
        applies=True,
        directive_type=kind,
        structured_adjustment=adjustment,
        explanation="Test directive.",
    )


def test_feasible_directives_report_no_conflict():
    result = diagnose_conflicts(scenario(), [])
    assert result.feasible is True
    assert result.conflicting_note_indices == ()


def test_pairwise_conflict_reports_both_notes():
    directives = [
        directive(0, "max_grid_window", {"hours": [0], "max_grid_kwh": 50.0}),
        directive(1, "no_discharge_window", {"hours": [0]}),
    ]
    result = diagnose_conflicts(scenario(), directives)
    assert result.feasible is False
    assert result.conflicting_note_indices == (0, 1)
    assert result.hours == (0,)
    assert len(result.suggestions) == 2


def test_single_impossible_directive_isolated():
    directives = [
        directive(0, "max_grid_window", {"hours": list(range(24)), "max_grid_kwh": 0.0}),
        directive(1, "no_discharge_window", {"hours": [3]}),
    ]
    result = diagnose_conflicts(scenario(), directives)
    assert result.feasible is False
    assert result.conflicting_note_indices == (0,)
