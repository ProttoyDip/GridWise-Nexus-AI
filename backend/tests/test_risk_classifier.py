"""Unit tests for app.llm.risk_classifier — no LLM, no network, no env.

Covers text-based tier classification, asset-keyword escalation, and
directive-based refinement (numeric directives -> MEDIUM, multiple
independent constraints -> HIGH).
"""

from app.llm.risk_classifier import (
    RiskLevel,
    classify_note_risk,
    classify_risk,
    refine_risk_with_directive,
)
from app.models.response import DirectiveInterpretation


def _di(
    directive_type: str,
    adjustment: dict | None,
    applies: bool | None = None,
) -> DirectiveInterpretation:
    return DirectiveInterpretation(
        note_index=0,
        applies=applies if applies is not None else directive_type != "no_op",
        directive_type=directive_type,
        structured_adjustment=adjustment,
        explanation="test",
    )


class TestTextClassification:
    def test_empty_note_is_low(self):
        assert classify_note_risk("") == RiskLevel.LOW

    def test_short_unrelated_note_is_low(self):
        assert classify_note_risk("hello there") == RiskLevel.LOW

    def test_unrelated_with_numbers_but_no_asset_is_low(self):
        # Numbers without any asset keyword carry no scheduling signal.
        assert classify_note_risk("today is day 7 of the semester") == RiskLevel.LOW

    def test_single_asset_mention_with_one_number_is_medium(self):
        assert classify_note_risk("solar output drops to 80%") == RiskLevel.MEDIUM

    def test_battery_with_one_number_is_medium(self):
        assert classify_note_risk("battery must stay above 100 kWh") == RiskLevel.MEDIUM

    def test_many_numeric_tokens_is_high(self):
        note = "charge from 1 to 4 with 5 kWh then 8 kWh then 12 kWh"
        assert classify_note_risk(note) == RiskLevel.HIGH

    def test_asset_with_three_numbers_is_high(self):
        note = "battery 100 kWh reserve, grid limit 50 kWh, solar 30 kWh"
        assert classify_note_risk(note) == RiskLevel.HIGH

    def test_multiple_time_window_cues_is_medium(self):
        note = "do not discharge from 5pm to 7pm and again from 8pm to 10pm"
        assert classify_note_risk(note) == RiskLevel.MEDIUM


class TestDirectiveRefinement:
    def test_no_op_does_not_escalate(self):
        assert refine_risk_with_directive(
            RiskLevel.LOW, _di("no_op", None, applies=False)
        ) == RiskLevel.LOW
        assert refine_risk_with_directive(
            RiskLevel.MEDIUM, _di("no_op", None, applies=False)
        ) == RiskLevel.MEDIUM

    def test_numeric_directive_promotes_low_to_medium(self):
        assert refine_risk_with_directive(
            RiskLevel.LOW,
            _di("solar_reduction", {"hours": [13], "factor": 0.2}),
        ) == RiskLevel.MEDIUM

    def test_wide_hour_window_alone_promotes_low_to_medium(self):
        # no_charge_window with 6+ hours but no numeric field: counts
        # as one constraint (the wide window) -> MEDIUM.
        assert refine_risk_with_directive(
            RiskLevel.LOW,
            _di("no_charge_window", {"hours": list(range(0, 12))}),
        ) == RiskLevel.MEDIUM

    def test_numeric_directive_with_wide_window_promotes_to_high(self):
        # numeric field + wide window = 2 constraints -> HIGH.
        assert refine_risk_with_directive(
            RiskLevel.LOW,
            _di("max_grid_window", {"hours": list(range(0, 18)), "max_grid_kwh": 50.0}),
        ) == RiskLevel.HIGH

    def test_high_base_stays_high(self):
        assert refine_risk_with_directive(
            RiskLevel.HIGH,
            _di("solar_reduction", {"hours": [13], "factor": 0.2}),
        ) == RiskLevel.HIGH


class TestClassifyRiskCombined:
    def test_low_text_low_directive(self):
        assert classify_risk("hi", _di("no_op", None, applies=False)) == RiskLevel.LOW

    def test_low_text_numeric_directive_promotes_to_medium(self):
        result = classify_risk(
            "ok", _di("solar_reduction", {"hours": [13], "factor": 0.2})
        )
        assert result == RiskLevel.MEDIUM

    def test_none_directive_falls_back_to_text(self):
        assert classify_risk("solar drops to 80%", None) == RiskLevel.MEDIUM
