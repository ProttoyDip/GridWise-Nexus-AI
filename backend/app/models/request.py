"""Request schema: scenario_id, operator_notes, hours[24], battery.

Mirrors Problem Statement Section 07 (Request Schema) exactly.
"""

from pydantic import BaseModel, Field, field_validator, model_validator


class HourEntry(BaseModel):
    hour: int = Field(..., ge=0, le=23)
    demand_kwh: float = Field(..., ge=0)
    solar_kwh: float = Field(..., ge=0)
    tariff_bdt_per_kwh: float = Field(..., ge=0)


class BatteryConfig(BaseModel):
    capacity_kwh: float = Field(..., gt=0)
    initial_energy_kwh: float = Field(..., ge=0)
    minimum_energy_kwh: float = Field(..., ge=0)
    max_charge_kwh_per_hour: float = Field(..., ge=0)
    max_discharge_kwh_per_hour: float = Field(..., ge=0)
    charge_efficiency: float = Field(1.0, gt=0, le=1)
    discharge_efficiency: float = Field(1.0, gt=0, le=1)
    degradation_cost_bdt_per_kwh: float = Field(0.0, ge=0)

    @model_validator(mode="after")
    def check_bounds(self) -> "BatteryConfig":
        if self.minimum_energy_kwh > self.capacity_kwh:
            raise ValueError("minimum_energy_kwh cannot exceed capacity_kwh")
        if not (self.minimum_energy_kwh <= self.initial_energy_kwh <= self.capacity_kwh):
            raise ValueError(
                "initial_energy_kwh must be between minimum_energy_kwh and capacity_kwh"
            )
        return self


class FlexibleLoad(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    energy_kwh: float = Field(..., gt=0)
    max_power_kwh_per_hour: float = Field(..., gt=0)
    earliest_hour: int = Field(..., ge=0, le=23)
    latest_hour: int = Field(..., ge=0, le=23)

    @model_validator(mode="after")
    def feasible_window(self) -> "FlexibleLoad":
        if self.latest_hour < self.earliest_hour:
            raise ValueError("latest_hour must be greater than or equal to earliest_hour")
        available = (self.latest_hour - self.earliest_hour + 1) * self.max_power_kwh_per_hour
        if self.energy_kwh > available:
            raise ValueError("flexible load energy exceeds the available window and power limit")
        return self


class ScenarioRequest(BaseModel):
    scenario_id: str = Field(..., min_length=1)
    operator_notes: list[str] = Field(..., min_length=1, max_length=3)
    hours: list[HourEntry] = Field(..., min_length=24, max_length=24)
    battery: BatteryConfig
    flexible_loads: list[FlexibleLoad] = Field(default_factory=list, max_length=12)

    @field_validator("operator_notes")
    @classmethod
    def notes_non_empty(cls, v: list[str]) -> list[str]:
        for note in v:
            if not note or not note.strip():
                raise ValueError("operator_notes entries must be non-empty strings")
        return v

    @field_validator("hours")
    @classmethod
    def hours_cover_0_to_23(cls, v: list[HourEntry]) -> list[HourEntry]:
        if len(v) != 24:
            raise ValueError("hours must contain exactly 24 entries")
        hour_values = sorted(h.hour for h in v)
        if hour_values != list(range(24)):
            raise ValueError("hours must contain each hour from 0 to 23 exactly once")
        return v

    @field_validator("flexible_loads")
    @classmethod
    def flexible_load_names_unique(cls, v: list[FlexibleLoad]) -> list[FlexibleLoad]:
        names = [load.name.casefold().strip() for load in v]
        if len(names) != len(set(names)):
            raise ValueError("flexible load names must be unique")
        return v
