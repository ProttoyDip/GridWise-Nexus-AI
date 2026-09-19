import { computeImpact } from "@/lib/impact";
import type { OptimizeResponse, Scenario } from "@/types";

export type RunRecord = {
  id: string;
  at: number;
  scenarioId: string;
  directive: string;
  directivesApplied: number;
  totalCostBdt: number;
  totalGridKwh: number;
  peakGridKwh: number;
  savedPct: number;
  co2AvoidedKg: number;
};

const KEY = "gridwise-run-history";
export const MAX_RUNS = 8;

export function loadRuns(): RunRecord[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(KEY) ?? "[]");
    return Array.isArray(parsed) ? (parsed as RunRecord[]).slice(0, MAX_RUNS) : [];
  } catch {
    return [];
  }
}

export function saveRuns(runs: RunRecord[]) {
  try {
    localStorage.setItem(KEY, JSON.stringify(runs.slice(0, MAX_RUNS)));
  } catch {
    /* storage unavailable */
  }
}

export function makeRun(scenario: Scenario, result: OptimizeResponse): RunRecord {
  const impact = computeImpact(scenario, result);
  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    at: Date.now(),
    scenarioId: scenario.scenario_id,
    directive: (scenario.operator_notes[0] ?? "").slice(0, 90),
    directivesApplied: result.directive_interpretation.filter((d) => d.applies).length,
    totalCostBdt: result.total_cost_bdt,
    totalGridKwh: result.total_grid_kwh,
    peakGridKwh: result.peak_grid_kwh,
    savedPct: impact.savedPct,
    co2AvoidedKg: impact.co2AvoidedKg,
  };
}

export function addRun(runs: RunRecord[], run: RunRecord): RunRecord[] {
  return [run, ...runs].slice(0, MAX_RUNS);
}

export type RunDelta = { label: string; a: number; b: number; unit: string; lowerIsBetter: boolean; digits: number };

export function compareRuns(a: RunRecord, b: RunRecord): RunDelta[] {
  return [
    { label: "Total cost", a: a.totalCostBdt, b: b.totalCostBdt, unit: "BDT", lowerIsBetter: true, digits: 0 },
    { label: "Grid energy", a: a.totalGridKwh, b: b.totalGridKwh, unit: "kWh", lowerIsBetter: true, digits: 0 },
    { label: "Peak grid load", a: a.peakGridKwh, b: b.peakGridKwh, unit: "kWh", lowerIsBetter: true, digits: 1 },
    { label: "Saved vs grid-only", a: a.savedPct, b: b.savedPct, unit: "%", lowerIsBetter: false, digits: 1 },
    { label: "CO₂ avoided", a: a.co2AvoidedKg, b: b.co2AvoidedKg, unit: "kg", lowerIsBetter: false, digits: 0 },
  ];
}
