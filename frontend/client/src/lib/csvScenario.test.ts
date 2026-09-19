import { describe, expect, it } from "vitest";
import { parseHourlyCsv } from "@/lib/csvScenario";

function csv(overrides: Partial<Record<number, string>> = {}) {
  return [
    "hour,demand_kwh,solar_kwh,tariff_bdt_per_kwh",
    ...Array.from({ length: 24 }, (_, hour) => overrides[hour] ?? `${hour},100,20,10`),
  ].join("\n");
}

describe("parseHourlyCsv", () => {
  it("loads and sorts a complete hourly profile", () => {
    const rows = parseHourlyCsv(csv());
    expect(rows).toHaveLength(24);
    expect(rows[0]).toEqual({ hour: 0, demand_kwh: 100, solar_kwh: 20, tariff_bdt_per_kwh: 10 });
    expect(rows[23].hour).toBe(23);
  });

  it("rejects negative meter values", () => {
    expect(() => parseHourlyCsv(csv({ 4: "4,-1,20,10" }))).toThrow("negative");
  });

  it("rejects a missing hour", () => {
    expect(() => parseHourlyCsv(csv({ 4: "5,100,20,10" }))).toThrow("every hour");
  });
});
