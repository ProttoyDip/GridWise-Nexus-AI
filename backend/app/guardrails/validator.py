"""Guardrail validation for LLM directive output.

Checks directive_type membership, note_index completeness/uniqueness,
ascending-unique hour arrays within 0-23, numeric ranges (solar factor
in [0,1], non-negative reserve/grid-cap values), and applies semantics
(no_op => applies=False, all others => applies=True) per Problem
Statement Section 08. Must fail safe (fallback to no_op) rather than
raise on malformed model output.
"""

# TODO: implement validate_directive_interpretation().
