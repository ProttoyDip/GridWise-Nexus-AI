import type { OptimizeResponse, Scenario } from "@/types";

/** Small deterministic scenario used by the unit tests: cheap night, peak evening, midday solar. */
export function makeScenario(): Scenario {
  return {
    scenario_id: "TEST-1",
    operator_notes: ["No special conditions"],
    battery: { capacity_kwh: 100, initial_energy_kwh: 50, minimum_energy_kwh: 20, max_charge_kwh_per_hour: 20, max_discharge_kwh_per_hour: 20 },
    hours: Array.from({ length: 24 }, (_, hour) => ({
      hour,
      demand_kwh: 100,
      solar_kwh: hour >= 10 && hour <= 14 ? 60 : 0,
      tariff_bdt_per_kwh: hour < 6 ? 5 : hour >= 17 && hour < 21 ? 20 : 10,
    })),
  };
}

export function makeResult(scenario: Scenario, overrides: Partial<Record<number, Partial<OptimizeResponse["hourly_plan"][number]>>> = {}): OptimizeResponse {
  const hourly_plan = scenario.hours.map((h) => {
    const base = { hour: h.hour, grid_kwh: h.demand_kwh - h.solar_kwh, solar_used_kwh: h.solar_kwh, battery_action: "idle", battery_kwh: 0, battery_energy_after_kwh: 50 };
    return { ...base, ...(overrides[h.hour] ?? {}) };
  });
  const total_grid_kwh = hourly_plan.reduce((sum, row) => sum + row.grid_kwh, 0);
  const total_cost_bdt = hourly_plan.reduce((sum, row) => sum + row.grid_kwh * scenario.hours[row.hour].tariff_bdt_per_kwh, 0);
  return {
    directive_interpretation: [{ note_index: 0, applies: false, directive_type: "no_op", structured_adjustment: null, explanation: "none" }],
    hourly_plan,
    total_grid_kwh,
    total_cost_bdt,
    peak_grid_kwh: Math.max(...hourly_plan.map((row) => row.grid_kwh)),
    plan_summary: "test plan",
  };
}
