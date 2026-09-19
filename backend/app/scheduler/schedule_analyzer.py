"""Groups an optimized 24h plan into contiguous behavioural windows."""

from __future__ import annotations

from typing import Any


def _get(row: Any, key: str, default: Any = 0) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def contiguous_windows(hours: list[int]) -> list[tuple[int, int]]:
    """[2,3,4,9] -> [(2,4),(9,9)] (inclusive hour ranges)."""
    windows: list[tuple[int, int]] = []
    for hour in sorted(set(hours)):
        if windows and hour == windows[-1][1] + 1:
            windows[-1] = (windows[-1][0], hour)
        else:
            windows.append((hour, hour))
    return windows


def analyze(plan: list[Any], hours: list[Any], battery: dict[str, Any]) -> dict[str, Any]:
    """Derive the signals the action generator needs from the real plan."""
    tariffs = {int(_get(h, "hour")): float(_get(h, "tariff_bdt_per_kwh")) for h in hours}
    solar = {int(_get(h, "hour")): float(_get(h, "solar_kwh")) for h in hours}
    grid = {int(_get(p, "hour")): float(_get(p, "grid_kwh")) for p in plan}
    action = {int(_get(p, "hour")): str(_get(p, "battery_action", "idle")) for p in plan}
    soc = {int(_get(p, "hour")): _get(p, "battery_energy_after_kwh", None) for p in plan}

    ordered = sorted(tariffs.values())
    low_cut = ordered[len(ordered) // 4] if ordered else 0.0
    median = ordered[len(ordered) // 2] if ordered else 0.0
    high_cut = max(ordered[(len(ordered) * 3) // 4], median + 1e-9) if ordered else 0.0
    peak_solar = max(solar.values(), default=0.0)
    reserve = float(battery.get("minimum_energy_kwh", 0))
    near_reserve = [h for h, v in soc.items() if v is not None and float(v) <= reserve + 0.5]

    return {
        "tariffs": tariffs,
        "solar": solar,
        "grid": grid,
        "battery_action": action,
        "low_tariff_cut": low_cut,
        "high_tariff_cut": high_cut,
        "peak_solar": peak_solar,
        "reserve_kwh": reserve,
        "near_reserve_hours": near_reserve,
        "charge_hours": [h for h, a in action.items() if a == "charge"],
        "discharge_hours": [h for h, a in action.items() if a == "discharge"],
        "solar_hours": [h for h, v in solar.items() if peak_solar > 0 and v >= 0.6 * peak_solar],
        "peak_grid_hour": max((h for h in grid if tariffs.get(h, 0) >= high_cut), key=grid.get, default=None),
    }
