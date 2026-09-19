"""Deterministic critical-load survival analysis during a grid outage."""

from __future__ import annotations

from app.models.request import ScenarioRequest


def evaluate_outage(
    scenario: ScenarioRequest,
    start_hour: int,
    end_hour: int,
    critical_load_fraction: float,
) -> dict:
    if not 0 <= start_hour <= end_hour <= 23:
        raise ValueError("outage hours must satisfy 0 <= start_hour <= end_hour <= 23")
    if not 0 < critical_load_fraction <= 1:
        raise ValueError("critical_load_fraction must be greater than 0 and at most 1")

    battery = scenario.battery
    energy = battery.initial_energy_kwh
    rows = []
    fully_served_hours = 0
    survival_intact = True
    total_critical = 0.0
    total_unserved = 0.0
    forecast = {entry.hour: entry for entry in scenario.hours}

    for hour in range(start_hour, end_hour + 1):
        entry = forecast[hour]
        critical = entry.demand_kwh * critical_load_fraction
        solar_to_load = min(entry.solar_kwh, critical)
        remaining = critical - solar_to_load
        available_battery = max(0.0, energy - battery.minimum_energy_kwh) * battery.discharge_efficiency
        discharge = min(remaining, available_battery, battery.max_discharge_kwh_per_hour)
        served = solar_to_load + discharge
        unserved = max(0.0, critical - served)
        energy -= discharge / battery.discharge_efficiency

        excess_solar = max(0.0, entry.solar_kwh - solar_to_load)
        charge = min(excess_solar, battery.max_charge_kwh_per_hour, battery.capacity_kwh - energy)
        energy += charge * battery.charge_efficiency
        if survival_intact and unserved <= 1e-6:
            fully_served_hours += 1
        elif unserved > 1e-6:
            survival_intact = False
        total_critical += critical
        total_unserved += unserved
        rows.append({
            "hour": hour,
            "critical_load_kwh": critical,
            "solar_used_kwh": solar_to_load,
            "battery_discharge_kwh": discharge,
            "solar_charge_kwh": charge,
            "unserved_kwh": unserved,
            "battery_energy_after_kwh": energy,
        })

    duration = end_hour - start_hour + 1
    return {
        "outage_start_hour": start_hour,
        "outage_end_hour": end_hour,
        "critical_load_fraction": critical_load_fraction,
        "fully_served": total_unserved <= 1e-6,
        "survival_hours": fully_served_hours,
        "outage_duration_hours": duration,
        "total_critical_load_kwh": total_critical,
        "unserved_energy_kwh": total_unserved,
        "minimum_battery_energy_kwh": min([battery.initial_energy_kwh, *(row["battery_energy_after_kwh"] for row in rows)]),
        "hourly": rows,
    }
