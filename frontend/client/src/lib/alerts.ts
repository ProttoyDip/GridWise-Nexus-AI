import { useSyncExternalStore } from "react";
import type { OptimizeResponse, Scenario } from "@/types";

export type AlertLevel = "warning" | "info" | "ok";
export type OpAlert = { id: string; level: AlertLevel; title: string; detail: string };

const hh = (hour: number) => `${String(hour % 24).padStart(2, "0")}:00`;

function windows(hours: number[]): Array<[number, number]> {
  const out: Array<[number, number]> = [];
  for (const hour of Array.from(new Set(hours)).sort((a, b) => a - b)) {
    const last = out[out.length - 1];
    if (last && hour === last[1] + 1) last[1] = hour;
    else out.push([hour, hour]);
  }
  return out;
}

const span = ([start, end]: [number, number]) => `${hh(start)}–${hh(end + 1)}`;

/** Derives operator alerts from the real plan; `nowHour` (0-23) marks windows starting within 3 hours as upcoming. */
export function deriveAlerts(scenario: Scenario, result: OptimizeResponse, nowHour: number): OpAlert[] {
  const alerts: OpAlert[] = [];
  const plan = result.hourly_plan;
  const forecast = new Map(scenario.hours.map((h) => [h.hour, h]));

  const reserve = scenario.battery.minimum_energy_kwh;
  windows(plan.filter((row) => (row.battery_energy_after_kwh ?? Infinity) <= reserve + 0.5).map((row) => row.hour)).forEach((win) => {
    alerts.push({ id: `reserve-${win[0]}`, level: "warning", title: `Battery at minimum reserve ${span(win)}`, detail: `Charge stays at ${reserve} kWh, so it cannot discharge further. Grid covers demand in this window.` });
  });

  const tariffs = scenario.hours.map((h) => h.tariff_bdt_per_kwh).sort((a, b) => a - b);
  const median = tariffs[Math.floor(tariffs.length / 2)] ?? 0;
  const cut = Math.max(tariffs[Math.floor((tariffs.length * 3) / 4)] ?? 0, median + 1e-9);
  windows(scenario.hours.filter((h) => h.tariff_bdt_per_kwh >= cut).map((h) => h.hour)).forEach((win) => {
    const covered = plan.filter((row) => row.hour >= win[0] && row.hour <= win[1] && row.battery_action === "discharge").reduce((sum, row) => sum + (row.battery_kwh ?? 0), 0);
    const upcoming = ((win[0] - nowHour + 24) % 24) <= 3 && win[0] !== nowHour;
    alerts.push({
      id: `peak-${win[0]}`,
      level: upcoming ? "warning" : "info",
      title: `${upcoming ? "Peak tariff starting soon: " : "Peak tariff "}${span(win)}`,
      detail: covered > 0 ? `Battery discharge covers ${Math.round(covered)} kWh of this window.` : "No battery discharge is planned here, so expect the highest energy cost.",
    });
  });

  const solarAvailable = scenario.hours.reduce((sum, h) => sum + h.solar_kwh, 0);
  const solarUsed = plan.reduce((sum, row) => sum + row.solar_used_kwh, 0);
  if (solarAvailable > 0 && solarUsed / solarAvailable < 0.7) {
    const curtailed = plan.filter((row) => (forecast.get(row.hour)?.solar_kwh ?? 0) > 0 && row.solar_used_kwh < 0.9 * (forecast.get(row.hour)?.solar_kwh ?? 0)).map((row) => row.hour);
    alerts.push({ id: "solar", level: "warning", title: `Only ${Math.round((solarUsed / solarAvailable) * 100)}% of forecast solar is usable`, detail: curtailed.length ? `Reduced around ${windows(curtailed).map(span).join(", ")}, usually from a solar-reduction directive.` : "Check the solar forecast and directives." });
  }

  const peak = plan.reduce((best, row) => (row.grid_kwh > best.grid_kwh ? row : best), plan[0]);
  if (peak) alerts.push({ id: "peak-grid", level: "info", title: `Highest grid import at ${hh(peak.hour)}`, detail: `${peak.grid_kwh.toFixed(0)} kWh, the day's peak. Watch the feeder limit.` });

  if (!alerts.some((a) => a.level === "warning")) alerts.unshift({ id: "clear", level: "ok", title: "No critical alerts", detail: "The plan is within battery, solar and grid limits." });
  return alerts.sort((a, b) => ({ warning: 0, info: 1, ok: 2 })[a.level] - ({ warning: 0, info: 1, ok: 2 })[b.level]);
}

let alertCount = 0;
const listeners = new Set<() => void>();

export function setAlertCount(count: number) {
  if (count === alertCount) return;
  alertCount = count;
  listeners.forEach((listener) => listener());
}

export function useAlertCount(): number {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => alertCount,
  );
}
