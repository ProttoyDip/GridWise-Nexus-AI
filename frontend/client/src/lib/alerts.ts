import type { OptimizeResponse, Scenario } from "@/types";

export type AlertLevel = "warning" | "info" | "ok";
export type AlertKind = "reserve" | "peak" | "solar" | "grid" | "clear";
export type OpAlert = {
  id: string;
  level: AlertLevel;
  kind: AlertKind;
  title: string;
  detail: string;
  /** Inclusive hour range the alert covers, when it is tied to a time window. */
  window?: [number, number];
};

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
    alerts.push({ id: `reserve-${win[0]}`, level: "warning", kind: "reserve", window: win, title: `Battery at minimum reserve ${span(win)}`, detail: `Charge stays at ${reserve} kWh, so it cannot discharge further. Grid covers demand in this window.` });
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
      kind: "peak",
      window: win,
      title: `${upcoming ? "Peak tariff starting soon: " : "Peak tariff "}${span(win)}`,
      detail: covered > 0 ? `Battery discharge covers ${Math.round(covered)} kWh of this window.` : "No battery discharge is planned here, so expect the highest energy cost.",
    });
  });

  const solarAvailable = scenario.hours.reduce((sum, h) => sum + h.solar_kwh, 0);
  const solarUsed = plan.reduce((sum, row) => sum + row.solar_used_kwh, 0);
  if (solarAvailable > 0 && solarUsed / solarAvailable < 0.7) {
    const curtailed = windows(plan.filter((row) => (forecast.get(row.hour)?.solar_kwh ?? 0) > 0 && row.solar_used_kwh < 0.9 * (forecast.get(row.hour)?.solar_kwh ?? 0)).map((row) => row.hour));
    alerts.push({
      id: "solar",
      level: "warning",
      kind: "solar",
      window: curtailed[0],
      title: `Only ${Math.round((solarUsed / solarAvailable) * 100)}% of forecast solar is usable`,
      detail: curtailed.length ? `Reduced around ${curtailed.map(span).join(", ")}, usually from a solar-reduction directive.` : "Check the solar forecast and directives.",
    });
  }

  const peak = plan.reduce((best, row) => (row.grid_kwh > best.grid_kwh ? row : best), plan[0]);
  if (peak) alerts.push({ id: "peak-grid", level: "info", kind: "grid", window: [peak.hour, peak.hour], title: `Highest grid import at ${hh(peak.hour)}`, detail: `${peak.grid_kwh.toFixed(0)} kWh, the day's peak. Watch the feeder limit.` });

  if (!alerts.some((a) => a.level === "warning")) alerts.unshift({ id: "clear", level: "ok", kind: "clear", title: "No critical alerts", detail: "The plan is within battery, solar and grid limits." });
  const order = { warning: 0, info: 1, ok: 2 };
  return alerts.sort((a, b) => order[a.level] - order[b.level]);
}

/** For each hour, the alert kinds that cover it (used by the 24-hour risk strip). */
export function hourCoverage(alerts: OpAlert[]): AlertKind[][] {
  return Array.from({ length: 24 }, (_, hour) =>
    Array.from(new Set(alerts.filter((a) => a.window && hour >= a.window[0] && hour <= a.window[1]).map((a) => a.kind))),
  );
}
