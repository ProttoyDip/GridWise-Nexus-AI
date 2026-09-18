"""Response schema: directive_interpretation[], hourly_plan[24], totals.

Mirrors Problem Statement Section 10 (Response Schema) exactly.
"""

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

DirectiveType = Literal[
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
]


def _check_ascending_unique_hours(hours: list[Any]) -> None:
    if not all(isinstance(h, int) and 0 <= h <= 23 for h in hours):
        raise ValueError("structured_adjustment hours must be integers from 0 through 23")
    if len(set(hours)) != len(hours):
        raise ValueError("structured_adjustment hours must be unique")
    if hours != sorted(hours):
        raise ValueError("structured_adjustment hours must be in ascending order")


class DirectiveInterpretation(BaseModel):
    note_index: int = Field(..., ge=0)
    applies: bool
    directive_type: DirectiveType
    structured_adjustment: Optional[dict[str, Any]] = None
    explanation: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def check_applies_semantics(self) -> "DirectiveInterpretation":
        if self.directive_type == "no_op":
            if self.applies:
                raise ValueError("no_op directives must have applies=False")
            if self.structured_adjustment is not None:
                raise ValueError("no_op directives must have structured_adjustment=None")
        else:
            if not self.applies:
                raise ValueError("non-no_op directives must have applies=True")
            if self.structured_adjustment is None:
                raise ValueError("non-no_op directives must include structured_adjustment")
        return self

    @model_validator(mode="after")
    def check_structured_adjustment_fields(self) -> "DirectiveInterpretation":
        adjustment = self.structured_adjustment
        if adjustment is None:
            return self

        hours = adjustment.get("hours")
        if hours is not None:
            _check_ascending_unique_hours(hours)

        if self.directive_type == "solar_reduction":
            factor = adjustment.get("factor")
            if factor is None:
                raise ValueError("solar_reduction requires a 'factor' field")
            if not (0 <= factor <= 1):
                raise ValueError("solar_reduction factor must be between 0 and 1")
        elif self.directive_type == "minimum_battery_reserve":
            if adjustment.get("minimum_energy_kwh") is None:
                raise ValueError("minimum_battery_reserve requires a 'minimum_energy_kwh' field")
            if adjustment["minimum_energy_kwh"] < 0:
                raise ValueError("minimum_energy_kwh must be non-negative")
        elif self.directive_type == "max_grid_window":
            if adjustment.get("max_grid_kwh") is None:
                raise ValueError("max_grid_window requires a 'max_grid_kwh' field")
            if adjustment["max_grid_kwh"] < 0:
                raise ValueError("max_grid_kwh must be non-negative")
        elif self.directive_type in ("no_charge_window", "no_discharge_window"):
            if hours is None:
                raise ValueError(f"{self.directive_type} requires an 'hours' field")

        return self


class HourlyPlanEntry(BaseModel):
    hour: int = Field(..., ge=0, le=23)
    grid_kwh: float = Field(..., ge=0)
    solar_used_kwh: float = Field(..., ge=0)
    battery_action: Literal["charge", "discharge", "idle"]
    battery_kwh: float = Field(..., ge=0)
    battery_energy_after_kwh: float = Field(..., ge=0)

    @model_validator(mode="after")
    def check_battery_kwh_matches_action(self) -> "HourlyPlanEntry":
        if self.battery_action == "idle" and self.battery_kwh != 0:
            raise ValueError("battery_kwh must be 0 when battery_action is idle")
        return self


class OptimizeResponse(BaseModel):
    scenario_id: str = Field(..., min_length=1)
    directive_interpretation: list[DirectiveInterpretation] = Field(..., min_length=1, max_length=3)
    hourly_plan: list[HourlyPlanEntry] = Field(..., min_length=24, max_length=24)
    total_grid_kwh: float = Field(..., ge=0)
    total_cost_bdt: float = Field(..., ge=0)
    peak_grid_kwh: float = Field(..., ge=0)
    plan_summary: str = Field(..., min_length=1)

    @field_validator("directive_interpretation")
    @classmethod
    def note_index_in_order(
        cls, v: list[DirectiveInterpretation]
    ) -> list[DirectiveInterpretation]:
        expected = list(range(len(v)))
        actual = [entry.note_index for entry in v]
        if actual != expected:
            raise ValueError(
                "directive_interpretation must contain exactly one entry per operator "
                "note, in note_index order starting from 0"
            )
        return v

    @field_validator("hourly_plan")
    @classmethod
    def hourly_plan_covers_0_to_23(cls, v: list[HourlyPlanEntry]) -> list[HourlyPlanEntry]:
        if len(v) != 24:
            raise ValueError("hourly_plan must contain exactly 24 entries")
        hour_values = sorted(entry.hour for entry in v)
        if hour_values != list(range(24)):
            raise ValueError("hourly_plan must contain each hour from 0 to 23 exactly once")
        return v

    @model_validator(mode="after")
    def check_peak_matches_plan(self) -> "OptimizeResponse":
        max_grid = max(entry.grid_kwh for entry in self.hourly_plan)
        if abs(self.peak_grid_kwh - max_grid) > 0.01:
            raise ValueError("peak_grid_kwh must match the maximum grid_kwh in hourly_plan")
        return self
