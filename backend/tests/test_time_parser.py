"""Deterministic time-normalization tests; no LLM or network involved.

Tests the standalone normalizer (normalize_time_expression) and its
integration with the guardrails validator (normalize_directive_hours).
"""

import pytest

from app.normalizer.time_parser import normalize_time_expression
from app.guardrails.validator import normalize_directive_hours


# ===================================================================
# Core normalizer — required test cases from the specification
# ===================================================================


class TestSpecifiedCases:
    """The four cases explicitly required by the user request."""

    def test_1pm_to_3pm(self):
        assert normalize_time_expression("1 PM to 3 PM") == [13, 14]

    def test_6pm_until_9pm(self):
        assert normalize_time_expression("6 PM until 9 PM") == [18, 19, 20]

    def test_midnight_to_2am(self):
        assert normalize_time_expression("midnight to 2 AM") == [0, 1]

    def test_from_noon_until_4pm(self):
        assert normalize_time_expression("from noon until 4 PM") == [12, 13, 14, 15]


# ===================================================================
# AM/PM range tests
# ===================================================================


class TestAMPMRanges:
    """AM/PM-style range expressions."""

    def test_9am_to_12pm(self):
        assert normalize_time_expression("9 AM to 12 PM") == [9, 10, 11]

    def test_12am_to_3am(self):
        assert normalize_time_expression("12 AM to 3 AM") == [0, 1, 2]

    def test_12pm_to_2pm(self):
        assert normalize_time_expression("12 PM to 2 PM") == [12, 13]

    def test_10pm_to_2am_midnight_crossing(self):
        result = normalize_time_expression("10 PM to 2 AM")
        assert result == [0, 1, 22, 23]

    def test_11pm_to_1am(self):
        result = normalize_time_expression("11 PM to 1 AM")
        assert result == [0, 23]

    def test_single_hour_range(self):
        assert normalize_time_expression("5 AM to 6 AM") == [5]


# ===================================================================
# 24-hour format tests
# ===================================================================


class TestTwentyFourHourFormat:
    """24-hour (military) time expressions."""

    def test_13_to_15(self):
        assert normalize_time_expression("13 to 15") == [13, 14]

    def test_0_to_6(self):
        assert normalize_time_expression("0 to 6") == [0, 1, 2, 3, 4, 5]

    def test_22_to_2_midnight_crossing(self):
        result = normalize_time_expression("22 to 2")
        assert result == [0, 1, 22, 23]


# ===================================================================
# Named period tests
# ===================================================================


class TestNamedPeriods:
    """Named period keywords."""

    def test_morning(self):
        assert normalize_time_expression("morning") == [6, 7, 8, 9, 10, 11]

    def test_afternoon(self):
        assert normalize_time_expression("afternoon") == [12, 13, 14, 15, 16]

    def test_evening(self):
        assert normalize_time_expression("evening") == [17, 18, 19, 20]

    def test_night(self):
        result = normalize_time_expression("night")
        assert result == [0, 1, 2, 3, 4, 5, 21, 22, 23]

    def test_overnight(self):
        result = normalize_time_expression("overnight")
        assert result == [0, 1, 2, 3, 4, 5, 21, 22, 23]


# ===================================================================
# Anchor keyword tests
# ===================================================================


class TestAnchorKeywords:
    """Ranges using named anchors (midnight, noon)."""

    def test_midnight_to_6am(self):
        assert normalize_time_expression("midnight to 6 AM") == [0, 1, 2, 3, 4, 5]

    def test_noon_to_6pm(self):
        assert normalize_time_expression("noon to 6 PM") == [12, 13, 14, 15, 16, 17]

    def test_from_midnight_to_noon(self):
        result = normalize_time_expression("from midnight to noon")
        assert result == list(range(0, 12))


# ===================================================================
# Keyword variations (from, until, through)
# ===================================================================


class TestKeywordVariations:
    """Different connector keywords."""

    def test_from_prefix(self):
        assert normalize_time_expression("from 1 PM to 3 PM") == [13, 14]

    def test_until_keyword(self):
        assert normalize_time_expression("2 PM until 5 PM") == [14, 15, 16]

    def test_through_keyword(self):
        assert normalize_time_expression("2 PM through 5 PM") == [14, 15, 16]


# ===================================================================
# Edge cases and error handling
# ===================================================================


class TestEdgeCases:
    """Boundary conditions and invalid inputs."""

    def test_same_start_end_raises(self):
        with pytest.raises(ValueError, match="empty hour set"):
            normalize_time_expression("3 PM to 3 PM")

    def test_unparseable_raises(self):
        with pytest.raises(ValueError, match="Could not parse"):
            normalize_time_expression("some random text")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="Could not parse"):
            normalize_time_expression("")

    def test_whitespace_handling(self):
        assert normalize_time_expression("  1 PM to 3 PM  ") == [13, 14]


# ===================================================================
# Guardrails integration tests
# ===================================================================


class TestGuardrailsIntegration:
    """normalize_directive_hours integration with the guardrails pipeline."""

    def test_time_expression_replaces_hours(self):
        data = {
            "directive_type": "no_charge_window",
            "structured_adjustment": {
                "hours": [],
                "time_expression": "1 PM to 3 PM",
            },
        }
        result = normalize_directive_hours(data)
        assert result["structured_adjustment"]["hours"] == [13, 14]
        assert "time_expression" not in result["structured_adjustment"]

    def test_time_expression_overrides_bad_llm_hours(self):
        """The LLM put wrong hours; the normalizer overrides them."""
        data = {
            "directive_type": "solar_reduction",
            "structured_adjustment": {
                "hours": [13, 14, 15],  # wrong: includes 15
                "time_expression": "1 PM to 3 PM",
                "factor": 0.5,
            },
        }
        result = normalize_directive_hours(data)
        assert result["structured_adjustment"]["hours"] == [13, 14]

    def test_no_time_expression_passes_through(self):
        """Without time_expression, hours are left as-is for normal validation."""
        data = {
            "directive_type": "solar_reduction",
            "structured_adjustment": {
                "hours": [13, 14, 15],
                "factor": 0.5,
            },
        }
        result = normalize_directive_hours(data)
        assert result["structured_adjustment"]["hours"] == [13, 14, 15]

    def test_non_dict_adjustment_passes_through(self):
        data = {"directive_type": "no_op", "structured_adjustment": None}
        assert normalize_directive_hours(data) == data

    def test_invalid_time_expression_removes_key(self):
        """Invalid expressions are removed; downstream validation rejects."""
        data = {
            "directive_type": "no_charge_window",
            "structured_adjustment": {
                "hours": [1, 2],
                "time_expression": "gibberish",
            },
        }
        result = normalize_directive_hours(data)
        # time_expression is removed, original hours preserved
        assert "time_expression" not in result["structured_adjustment"]
        assert result["structured_adjustment"]["hours"] == [1, 2]

