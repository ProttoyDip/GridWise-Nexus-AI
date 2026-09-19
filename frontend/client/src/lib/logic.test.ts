import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { pipelineStates } from "@/components/ControlCenterPanels";
import { deriveAlerts, hourCoverage } from "@/lib/alerts";
import { actionsToCsv, actionsToText } from "@/lib/exportPlan";
import { makeResult, makeScenario } from "@/lib/fixtures";
import { addRun, compareRuns, loadRuns, makeRun, MAX_RUNS, saveRuns } from "@/lib/history";
import { computeImpact, GRID_EMISSION_KG_PER_KWH } from "@/lib/impact";
import { optimizeWithProgress } from "@/lib/optimizeStream";
import type { ScheduledAction } from "@/types";

describe("computeImpact", () => {
  it("compares the plan with buying every kWh from the grid", () => {
    const scenario = makeScenario();
    const impact = computeImpact(scenario, makeResult(scenario));
    const baseline = scenario.hours.reduce((sum, h) => sum + h.demand_kwh * h.tariff_bdt_per_kwh, 0);
    expect(impact.baselineCostBdt).toBe(baseline);
    expect(impact.gridAvoidedKwh).toBe(5 * 60);
    expect(impact.co2AvoidedKg).toBeCloseTo(300 * GRID_EMISSION_KG_PER_KWH);
    expect(impact.solarUsedPct).toBe(100);
    expect(impact.savedBdt).toBeGreaterThan(0);
  });

  it("never reports negative carbon savings", () => {
    const scenario = makeScenario();
    const result = makeResult(scenario, { 3: { grid_kwh: 500 } });
    expect(computeImpact(scenario, result).co2AvoidedKg).toBeGreaterThanOrEqual(0);
  });
});

describe("deriveAlerts", () => {
  it("warns when the battery sits at its minimum reserve", () => {
    const scenario = makeScenario();
    const result = makeResult(scenario, { 1: { battery_energy_after_kwh: 20 }, 2: { battery_energy_after_kwh: 20 } });
    const alerts = deriveAlerts(scenario, result, 12);
    expect(alerts[0]).toMatchObject({ level: "warning", title: "Battery at minimum reserve 01:00–03:00" });
  });

  it("escalates a peak-tariff window that starts within three hours", () => {
    const scenario = makeScenario();
    const soon = deriveAlerts(scenario, makeResult(scenario), 15).find((a) => a.id === "peak-17");
    const later = deriveAlerts(scenario, makeResult(scenario), 8).find((a) => a.id === "peak-17");
    expect(soon?.level).toBe("warning");
    expect(later?.level).toBe("info");
  });

  it("reports all clear when nothing needs attention", () => {
    const scenario = makeScenario();
    const alerts = deriveAlerts(scenario, makeResult(scenario), 8);
    expect(alerts.some((a) => a.level === "warning")).toBe(false);
    expect(alerts.some((a) => a.id === "clear")).toBe(true);
  });

  it("flags heavily curtailed solar", () => {
    const scenario = makeScenario();
    const overrides = Object.fromEntries([10, 11, 12, 13, 14].map((h) => [h, { solar_used_kwh: 6 }]));
    const alerts = deriveAlerts(scenario, makeResult(scenario, overrides), 8);
    expect(alerts.find((a) => a.id === "solar")?.title).toContain("10%");
  });
});

describe("alert windows", () => {
  it("attaches inclusive hour windows and maps them onto a 24-hour coverage strip", () => {
    const scenario = makeScenario();
    const result = makeResult(scenario, { 1: { battery_energy_after_kwh: 20 }, 2: { battery_energy_after_kwh: 20 } });
    const alerts = deriveAlerts(scenario, result, 8);
    const reserve = alerts.find((alert) => alert.kind === "reserve");
    expect(reserve?.window).toEqual([1, 2]);
    const coverage = hourCoverage(alerts);
    expect(coverage).toHaveLength(24);
    expect(coverage[1]).toContain("reserve");
    expect(coverage[2]).toContain("reserve");
    expect(coverage[3]).not.toContain("reserve");
    expect(coverage[17]).toContain("peak");
    expect(coverage[5]).toEqual([]);
  });

  it("gives the all-clear alert no window so it never paints the strip", () => {
    const scenario = makeScenario();
    const clear = deriveAlerts(scenario, makeResult(scenario), 8).find((alert) => alert.kind === "clear");
    expect(clear?.window).toBeUndefined();
  });
});

describe("export", () => {
  const actions: ScheduledAction[] = [
    { type: "BATTERY_CHARGE", start_time: "02:00", end_time: "05:00", priority: "HIGH", reason: 'Low "night" tariff', expected_impact: "Lower cost" },
    { type: "BATTERY_DISCHARGE", start_time: "18:00", end_time: "20:00", priority: "HIGH", reason: "Peak", expected_impact: "Lower peak" },
  ];

  it("escapes quotes in CSV cells", () => {
    const csv = actionsToCsv(actions);
    expect(csv.split("\r\n")[0]).toBe("Start,End,Action,Priority,Reason,Impact");
    expect(csv).toContain('"Low ""night"" tariff"');
  });

  it("labels high-priority discharge as peak shaving in text", () => {
    expect(actionsToText("S1", actions)).toContain("18:00-20:00  Peak Shaving [HIGH]");
  });
});

describe("run history", () => {
  const store = new Map<string, string>();
  beforeEach(() => {
    store.clear();
    vi.stubGlobal("localStorage", { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v) });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("keeps only the most recent runs and round-trips through storage", () => {
    const scenario = makeScenario();
    let runs = loadRuns();
    for (let i = 0; i < MAX_RUNS + 3; i += 1) runs = addRun(runs, makeRun(scenario, makeResult(scenario)));
    saveRuns(runs);
    expect(loadRuns()).toHaveLength(MAX_RUNS);
  });

  it("survives corrupt storage", () => {
    store.set("gridwise-run-history", "{not json");
    expect(loadRuns()).toEqual([]);
  });

  it("compares two runs", () => {
    const scenario = makeScenario();
    const a = makeRun(scenario, makeResult(scenario));
    const b = makeRun(scenario, makeResult(scenario, { 3: { grid_kwh: 200 } }));
    const cost = compareRuns(a, b).find((row) => row.label === "Total cost");
    expect(cost && cost.b - cost.a).toBeGreaterThan(0);
  });
});

describe("pipelineStates", () => {
  it("advances with real backend stages while loading", () => {
    expect(pipelineStates({}, { loading: true, hasResult: false, scheduled: false })).toEqual(["done", "running", "idle", "idle", "idle", "idle"]);
    expect(pipelineStates({ 1: "completed", 3: "completed" }, { loading: true, hasResult: false, scheduled: false })).toEqual(["done", "done", "done", "running", "idle", "idle"]);
  });

  it("is fully done only after the result and the schedule are ready", () => {
    const stages = { 1: "completed", 3: "completed", 4: "completed", 5: "completed" } as const;
    expect(pipelineStates(stages, { loading: false, hasResult: true, scheduled: false }).at(-1)).toBe("idle");
    expect(pipelineStates(stages, { loading: false, hasResult: true, scheduled: true })).toEqual(Array(6).fill("done"));
  });

  it("marks everything done for a non-streamed result", () => {
    expect(pipelineStates({}, { loading: false, hasResult: true, scheduled: true })).toEqual(Array(6).fill("done"));
  });
});

describe("optimizeWithProgress", () => {
  afterEach(() => vi.unstubAllGlobals());

  const streamOf = (text: string, chunk = 7) => {
    const bytes = new TextEncoder().encode(text);
    return new ReadableStream<Uint8Array>({
      start(controller) {
        for (let i = 0; i < bytes.length; i += chunk) controller.enqueue(bytes.slice(i, i + chunk));
        controller.close();
      },
    });
  };

  it("reports stages and returns the result even when lines are split across chunks", async () => {
    const lines = [{ event: "progress", stage: 1, state: "running" }, { event: "heartbeat" }, { event: "progress", stage: 1, state: "completed" }, { event: "result", data: { ok: true } }];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(streamOf(lines.map((l) => JSON.stringify(l)).join("\n") + "\n"), { status: 200 })));
    const seen: string[] = [];
    const result = await optimizeWithProgress("http://api", makeScenario(), (stage, state) => seen.push(`${stage}:${state}`));
    expect(result).toEqual({ ok: true });
    expect(seen).toEqual(["1:running", "1:completed"]);
  });

  it("falls back to the plain endpoint when streaming is not available", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response("{}", { status: 404 })).mockResolvedValueOnce(new Response(JSON.stringify({ plan: 1 }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(optimizeWithProgress("http://api", makeScenario(), () => {})).resolves.toEqual({ plan: 1 });
    expect(fetchMock.mock.calls[1][0]).toBe("http://api/optimize-energy");
  });

  it("surfaces a streamed error message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(streamOf(JSON.stringify({ event: "error", detail: "Optimization failed: infeasible" }) + "\n"), { status: 200 })));
    await expect(optimizeWithProgress("http://api", makeScenario(), () => {})).rejects.toThrow("infeasible");
  });

  it("surfaces the API detail for non-OK stream responses", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "bad scenario" }), { status: 422 })));
    await expect(optimizeWithProgress("http://api", makeScenario(), () => {})).rejects.toThrow("bad scenario");
  });
});
