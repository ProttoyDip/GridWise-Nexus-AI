import type { HourInput } from "@/types";

function splitCsvLine(line: string): string[] {
  const values: string[] = [];
  let current = "";
  let quoted = false;
  for (let index = 0; index < line.length; index += 1) {
    const char = line[index];
    if (char === '"' && line[index + 1] === '"' && quoted) {
      current += '"';
      index += 1;
    } else if (char === '"') {
      quoted = !quoted;
    } else if (char === "," && !quoted) {
      values.push(current.trim());
      current = "";
    } else {
      current += char;
    }
  }
  if (quoted) throw new Error("CSV contains an unclosed quoted value.");
  values.push(current.trim());
  return values;
}

const aliases = {
  hour: ["hour", "time", "interval"],
  demand: ["demand_kwh", "demand", "load_kwh", "load"],
  solar: ["solar_kwh", "solar", "pv_kwh", "pv"],
  tariff: ["tariff_bdt_per_kwh", "tariff", "price_bdt_per_kwh", "price"],
};

function findColumn(headers: string[], names: string[]): number {
  const index = headers.findIndex((header) => names.includes(header));
  if (index < 0) throw new Error(`Missing column. Expected one of: ${names.join(", ")}.`);
  return index;
}

export function parseHourlyCsv(source: string): HourInput[] {
  const lines = source.replace(/^\uFEFF/, "").split(/\r?\n/).filter((line) => line.trim());
  if (lines.length !== 25) throw new Error("CSV must contain one header row and exactly 24 hourly rows.");
  const headers = splitCsvLine(lines[0]).map((header) => header.toLowerCase().trim().replaceAll(" ", "_"));
  const columns = {
    hour: findColumn(headers, aliases.hour),
    demand: findColumn(headers, aliases.demand),
    solar: findColumn(headers, aliases.solar),
    tariff: findColumn(headers, aliases.tariff),
  };
  const rows = lines.slice(1).map((line, rowIndex) => {
    const values = splitCsvLine(line);
    const rawHour = values[columns.hour] ?? "";
    const hour = Number(rawHour.includes(":") ? rawHour.split(":")[0] : rawHour);
    const demand_kwh = Number(values[columns.demand]);
    const solar_kwh = Number(values[columns.solar]);
    const tariff_bdt_per_kwh = Number(values[columns.tariff]);
    if (![hour, demand_kwh, solar_kwh, tariff_bdt_per_kwh].every(Number.isFinite)) {
      throw new Error(`Row ${rowIndex + 2} contains a missing or non-numeric value.`);
    }
    if (!Number.isInteger(hour) || hour < 0 || hour > 23) throw new Error(`Row ${rowIndex + 2} has an invalid hour.`);
    if (demand_kwh < 0 || solar_kwh < 0 || tariff_bdt_per_kwh < 0) throw new Error(`Row ${rowIndex + 2} contains a negative value.`);
    return { hour, demand_kwh, solar_kwh, tariff_bdt_per_kwh };
  });
  rows.sort((left, right) => left.hour - right.hour);
  if (rows.some((row, index) => row.hour !== index)) throw new Error("CSV must include every hour from 0 through 23 exactly once.");
  return rows;
}
