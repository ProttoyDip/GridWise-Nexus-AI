import type { OptimizeResponse, Scenario } from "@/types";

// Approximate grid emission factor (kg CO2 per kWh); an estimate, not a measurement.
export const GRID_EMISSION_KG_PER_KWH = 0.6;

export type Impact = {
  baselineCostBdt: number;
  savedBdt: number;
  savedPct: number;
  gridAvoidedKwh: number;
  co2AvoidedKg: number;
  solarUsedPct: number;
};

export function computeImpact(scenario: Scenario, result: OptimizeResponse): Impact {
  const tariffs = new Map(scenario.hours.map((h) => [h.hour, h.tariff_bdt_per_kwh]));
  const baselineCostBdt = scenario.hours.reduce((sum, h) => sum + h.demand_kwh * h.tariff_bdt_per_kwh, 0);
  const baselineGridKwh = scenario.hours.reduce((sum, h) => sum + h.demand_kwh, 0);
  const planCost = result.hourly_plan.reduce((sum, row) => sum + row.grid_kwh * (tariffs.get(row.hour) ?? 0), 0);
  const solarAvailable = scenario.hours.reduce((sum, h) => sum + h.solar_kwh, 0);
  const solarUsed = result.hourly_plan.reduce((sum, row) => sum + row.solar_used_kwh, 0);
  const gridAvoidedKwh = baselineGridKwh - result.total_grid_kwh;
  return {
    baselineCostBdt,
    savedBdt: baselineCostBdt - planCost,
    savedPct: baselineCostBdt > 0 ? ((baselineCostBdt - planCost) / baselineCostBdt) * 100 : 0,
    gridAvoidedKwh,
    co2AvoidedKg: Math.max(0, gridAvoidedKwh) * GRID_EMISSION_KG_PER_KWH,
    solarUsedPct: solarAvailable > 0 ? (solarUsed / solarAvailable) * 100 : 0,
  };
}
