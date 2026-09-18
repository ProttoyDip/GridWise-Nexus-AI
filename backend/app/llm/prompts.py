"""Prompt templates for operator-note interpretation.

Builds the system/user prompt that instructs the model to map a single
operator note to one of the allowed directive types (or no_op), as a
strict JSON object. The model's output is untrusted until it passes
pydantic validation (app.models.response.DirectiveInterpretation) and,
later, the deterministic guardrails layer — these prompts exist to
maximize the odds of first-try-valid output, not to be trusted alone.
"""

from __future__ import annotations

from app.models.request import BatteryConfig, HourEntry

ALLOWED_DIRECTIVE_TYPES = (
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
)

SYSTEM_PROMPT = """You are the operator-note interpreter for GridWise, a campus energy \
scheduling system. You convert one natural-language operator note at a time into a single \
structured, machine-checkable directive describing how it should affect today's 24-hour \
energy schedule.

Allowed directive_type values (use exactly one, nothing else):
- solar_reduction: usable solar is reduced to a fraction of forecast for some hours.
- minimum_battery_reserve: the battery must keep at least a given kWh level during some hours.
- no_charge_window: the battery must not charge during some hours.
- no_discharge_window: the battery must not discharge during some hours.
- max_grid_window: grid import must not exceed a given kWh cap during some hours.
- no_op: the note has no effect on today's 24-hour energy schedule (e.g. unrelated to \
energy/power/battery/solar/grid operations, or about a future/past date, or purely informational).

Rules:
1. Output exactly one JSON object and nothing else: no markdown fences, no commentary, no \
leading or trailing text.
2. JSON keys, in this exact set: "note_index", "applies", "directive_type", \
"structured_adjustment", "explanation".
3. If directive_type is "no_op": "applies" must be false and "structured_adjustment" must be \
JSON null.
4. If directive_type is anything other than "no_op": "applies" must be true and \
"structured_adjustment" must be a JSON object (never null).
5. "structured_adjustment.hours", when present, must be a JSON array of unique integers from \
0 through 23 in strictly ascending order. Time windows are start-inclusive, end-exclusive: \
"1 PM to 3 PM" means hours [13, 14], NOT [13, 14, 15].
6. structured_adjustment shape per directive_type:
   - solar_reduction: {"hours": [...], "factor": <0..1>} where factor is the USABLE fraction \
remaining after the reduction. An "80% reduction" or "80% drop" means factor = 0.2. A note that \
already states the remaining percentage (e.g. "solar will be at 25%") means factor = 0.25.
   - minimum_battery_reserve: {"hours": [...], "minimum_energy_kwh": <kWh>}. If the note gives a \
percentage of capacity (e.g. "keep at least 50% of the battery"), convert it to kWh using the \
battery capacity given below and round sensibly.
   - no_charge_window: {"hours": [...]}
   - no_discharge_window: {"hours": [...]}
   - max_grid_window: {"hours": [...], "max_grid_kwh": <kWh>}
7. Only mark applies=true when the note describes a real operating condition for TODAY's 24-hour \
window (solar availability, battery charge/discharge/reserve rules, or a grid import cap). Notes \
about unrelated topics (announcements, other departments, future dates, deadlines, etc.) are \
always no_op.
8. "explanation" is a short, free-text, human-readable justification (one to two sentences). Its \
wording is never checked exactly, only its presence.
9. Never invent hours, numbers, or constraints that are not stated or directly computable from the \
note and the battery capacity provided to you.
"""

USER_PROMPT_TEMPLATE = """Battery configuration:
- capacity_kwh: {capacity_kwh}
- initial_energy_kwh: {initial_energy_kwh}
- minimum_energy_kwh (hard floor at all times): {minimum_energy_kwh}
- max_charge_kwh_per_hour: {max_charge_kwh_per_hour}
- max_discharge_kwh_per_hour: {max_discharge_kwh_per_hour}

24-hour forecast (hour: demand_kwh, solar_kwh, tariff_bdt_per_kwh):
{hours_table}

Operator note (index {note_index}):
\"\"\"{note}\"\"\"

Interpret ONLY this note. Respond with exactly one JSON object for note_index {note_index}, \
following the schema and rules from the system prompt.
"""


def _format_hours_table(hours: list[HourEntry]) -> str:
    rows = sorted(hours, key=lambda h: h.hour)
    return "\n".join(
        f"{h.hour:02d}: demand={h.demand_kwh}, solar={h.solar_kwh}, tariff={h.tariff_bdt_per_kwh}"
        for h in rows
    )


def build_user_prompt(
    note: str,
    note_index: int,
    hours: list[HourEntry],
    battery: BatteryConfig,
) -> str:
    """Build the per-note interpretation prompt with full scenario context."""
    return USER_PROMPT_TEMPLATE.format(
        capacity_kwh=battery.capacity_kwh,
        initial_energy_kwh=battery.initial_energy_kwh,
        minimum_energy_kwh=battery.minimum_energy_kwh,
        max_charge_kwh_per_hour=battery.max_charge_kwh_per_hour,
        max_discharge_kwh_per_hour=battery.max_discharge_kwh_per_hour,
        hours_table=_format_hours_table(hours),
        note_index=note_index,
        note=note.strip(),
    )
