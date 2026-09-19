import {
  Copy,
  FileJson,
  Library,
  Plus,
  RotateCcw,
  Sparkles,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { useRef, useState } from "react";
import type { BatteryConfig, FlexibleLoad, Scenario } from "@/types";
import samplePack from "@/data/sampleCases.json";
import { JsonLoaderDialog } from "@/components/JsonLoaderDialog";
import { parseHourlyCsv } from "@/lib/csvScenario";

type SamplePackCase = { id: string; label: string; input: Scenario };
const samplePackCases = (samplePack as { cases: SamplePackCase[] }).cases;

type ScenarioBuilderProps = {
  scenario: Scenario;
  onChange: (next: Scenario) => void;
  onRandomize: () => void;
  onLoadJson: (value: string) => string | null;
  onLoadSampleCase: (caseId: string) => void;
  sampleJson: string;
};

const numberFields: Array<{
  key: keyof BatteryConfig;
  label: string;
  suffix: string;
}> = [
  { key: "capacity_kwh", label: "Capacity", suffix: "kWh" },
  { key: "initial_energy_kwh", label: "Initial energy", suffix: "kWh" },
  { key: "minimum_energy_kwh", label: "Minimum reserve", suffix: "kWh" },
  { key: "max_charge_kwh_per_hour", label: "Max charge / hr", suffix: "kWh" },
  { key: "max_discharge_kwh_per_hour", label: "Max discharge / hr", suffix: "kWh" },
  { key: "charge_efficiency", label: "Charge efficiency", suffix: "0–1" },
  { key: "discharge_efficiency", label: "Discharge efficiency", suffix: "0–1" },
  { key: "degradation_cost_bdt_per_kwh", label: "Battery wear cost", suffix: "BDT/kWh" },
];

const batteryDefaults: Partial<Record<keyof BatteryConfig, number>> = {
  charge_efficiency: 1,
  discharge_efficiency: 1,
  degradation_cost_bdt_per_kwh: 0,
};

export function ScenarioBuilder({
  scenario,
  onChange,
  onRandomize,
  onLoadJson,
  onLoadSampleCase,
  sampleJson,
}: ScenarioBuilderProps) {
  const [sampleOpen, setSampleOpen] = useState(false);
  const [jsonValue, setJsonValue] = useState(sampleJson);
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [csvStatus, setCsvStatus] = useState<{ ok: boolean; message: string } | null>(null);
  const csvInput = useRef<HTMLInputElement>(null);

  const updateHour = (
    index: number,
    key: "demand_kwh" | "solar_kwh" | "tariff_bdt_per_kwh",
    value: string,
  ) => {
    const hours = scenario.hours.map((row, rowIndex) =>
      rowIndex === index ? { ...row, [key]: Number(value) } : row,
    );
    onChange({ ...scenario, hours });
  };

  const updateBattery = (key: keyof BatteryConfig, value: string) => {
    onChange({
      ...scenario,
      battery: { ...scenario.battery, [key]: Number(value) },
    });
  };

  const updateNote = (index: number, value: string) => {
    const operator_notes = scenario.operator_notes.map((note, noteIndex) =>
      noteIndex === index ? value : note,
    );
    onChange({ ...scenario, operator_notes });
  };

  const addNote = () => {
    if (scenario.operator_notes.length < 3) {
      onChange({ ...scenario, operator_notes: [...scenario.operator_notes, ""] });
    }
  };

  const removeNote = (index: number) => {
    if (scenario.operator_notes.length > 1) {
      onChange({
        ...scenario,
        operator_notes: scenario.operator_notes.filter((_, i) => i !== index),
      });
    }
  };

  const updateFlexibleLoad = (index: number, patch: Partial<FlexibleLoad>) => {
    const flexible_loads = [...(scenario.flexible_loads ?? [])];
    flexible_loads[index] = { ...flexible_loads[index], ...patch };
    onChange({ ...scenario, flexible_loads });
  };

  const addFlexibleLoad = () => {
    const current = scenario.flexible_loads ?? [];
    if (current.length >= 12) return;
    const next = current.length + 1;
    onChange({
      ...scenario,
      flexible_loads: [
        ...current,
        { name: `Flexible task ${next}`, energy_kwh: 20, max_power_kwh_per_hour: 10, earliest_hour: 8, latest_hour: 17 },
      ],
    });
  };

  const removeFlexibleLoad = (index: number) => {
    onChange({
      ...scenario,
      flexible_loads: (scenario.flexible_loads ?? []).filter((_, itemIndex) => itemIndex !== index),
    });
  };

  const copyJson = async () => {
    await navigator.clipboard?.writeText(JSON.stringify(scenario, null, 2));
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  };

  const loadCsv = async (file: File | undefined) => {
    if (!file) return;
    try {
      const hours = parseHourlyCsv(await file.text());
      onChange({ ...scenario, hours });
      setCsvStatus({ ok: true, message: `Loaded 24 hourly rows from ${file.name}.` });
    } catch (reason) {
      setCsvStatus({ ok: false, message: reason instanceof Error ? reason.message : "Could not read this CSV file." });
    } finally {
      if (csvInput.current) csvInput.current.value = "";
    }
  };

  return (
    <section className="glass-card builder-card">
      <div className="panel-heading">
        <div>
          <div className="eyebrow"><span className="eyebrow-dot" /> Scenario input</div>
          <h2>Build an operating case</h2>
          <p>Shape the dispatch window, battery constraints, and operator intent.</p>
        </div>
        <div className="panel-actions">
          <input ref={csvInput} className="visually-hidden" type="file" accept=".csv,text/csv" onChange={(event) => void loadCsv(event.target.files?.[0])} />
          <button className="button button-quiet" type="button" onClick={() => csvInput.current?.click()} title="Import hourly profile CSV">
            <Upload size={15} /> Import CSV
          </button>
          <button className="button button-quiet" type="button" onClick={copyJson} title="Copy scenario JSON">
            {copied ? <Sparkles size={15} /> : <Copy size={15} />}
            {copied ? "Copied" : "Copy JSON"}
          </button>
          <button className="button button-quiet" type="button" onClick={onRandomize}>
            <RotateCcw size={15} /> Randomize
          </button>
        </div>
      </div>

      {csvStatus && <div className={`csv-status ${csvStatus.ok ? "is-ok" : "is-error"}`} role="status">{csvStatus.message}<button type="button" onClick={() => setCsvStatus(null)} aria-label="Dismiss CSV message"><X size={13} /></button></div>}

      <div className="scenario-meta-grid">
        <label className="field-label">
          <span>Scenario ID</span>
          <div className="input-shell input-shell-wide">
            <span className="input-prefix">ID</span>
            <input
              aria-label="Scenario ID"
              value={scenario.scenario_id}
              onChange={(event) => onChange({ ...scenario, scenario_id: event.target.value })}
              placeholder="north-grid-ops-01"
            />
          </div>
        </label>
        <label className="field-label">
          <span><Library size={12} style={{ display: "inline", marginRight: 4, verticalAlign: -1 }} /> Public sample pack</span>
          <div className="input-shell input-shell-wide">
            <select
              aria-label="Load a public sample case"
              value=""
              onChange={(event) => {
                if (event.target.value) onLoadSampleCase(event.target.value);
              }}
            >
              <option value="" disabled>Load a sample case ({samplePackCases.length} available)</option>
              {samplePackCases.map((sampleCase) => (
                <option key={sampleCase.id} value={sampleCase.id}>{sampleCase.id} — {sampleCase.label}</option>
              ))}
            </select>
          </div>
        </label>
        <div className="json-loader-wrap">
          <button className="button button-outline" type="button" onClick={() => setSampleOpen(true)}>
            <FileJson size={16} /> Paste JSON
          </button>
          <JsonLoaderDialog
            open={sampleOpen}
            value={jsonValue}
            onChange={setJsonValue}
            error={jsonError}
            onApply={() => {
              const message = onLoadJson(jsonValue);
              setJsonError(message);
              if (!message) setSampleOpen(false);
            }}
            onClose={() => { setSampleOpen(false); setJsonError(null); }}
          />
        </div>
      </div>

      <div className="section-divider"><span>Operator notes</span><span className="divider-line" /></div>
      <div className="notes-stack">
        {scenario.operator_notes.map((note, index) => (
          <div className="note-row" key={`note-${index}`}>
            <div className="note-index">0{index + 1}</div>
            <input
              id={`operator-note-${index}`}
              aria-label={`Operator note ${index + 1}`}
              value={note}
              onChange={(event) => updateNote(index, event.target.value)}
              placeholder={index === 0 ? "e.g. Protect reserve during evening peak" : "Add an operational preference"}
            />
            <button className="icon-button danger-hover" type="button" onClick={() => removeNote(index)} disabled={scenario.operator_notes.length === 1} aria-label="Remove note"><Trash2 size={15} /></button>
          </div>
        ))}
        <button className="button button-add-note" type="button" onClick={addNote} disabled={scenario.operator_notes.length === 3}>
          <Plus size={15} /> Add note <span className="limit-count">{scenario.operator_notes.length}/3</span>
        </button>
      </div>

      <div className="section-divider"><span>Battery configuration</span><span className="divider-line" /></div>
      <div className="battery-grid">
        {numberFields.map(({ key, label, suffix }) => (
          <label className="field-label" key={key}>
            <span>{label}</span>
            <div className="input-shell">
              <input type="number" step="0.01" value={scenario.battery[key] ?? batteryDefaults[key] ?? 0} onChange={(event) => updateBattery(key, event.target.value)} />
              <span className="input-suffix">{suffix}</span>
            </div>
          </label>
        ))}
      </div>

      <div className="section-divider"><span>Flexible equipment</span><span className="divider-line" /><span className="table-badge">{scenario.flexible_loads?.length ?? 0} tasks</span></div>
      <div className="flexible-load-list">
        {(scenario.flexible_loads ?? []).map((load, index) => (
          <div className="flexible-load-row" key={`${load.name}-${index}`}>
            <label><span>Task</span><input aria-label={`Flexible task ${index + 1} name`} value={load.name} onChange={(event) => updateFlexibleLoad(index, { name: event.target.value })} /></label>
            <label><span>Energy</span><div className="input-shell"><input type="number" min="0.1" step="0.1" value={load.energy_kwh} onChange={(event) => updateFlexibleLoad(index, { energy_kwh: Number(event.target.value) })} /><span className="input-suffix">kWh</span></div></label>
            <label><span>Max / hr</span><div className="input-shell"><input type="number" min="0.1" step="0.1" value={load.max_power_kwh_per_hour} onChange={(event) => updateFlexibleLoad(index, { max_power_kwh_per_hour: Number(event.target.value) })} /><span className="input-suffix">kWh</span></div></label>
            <label><span>Earliest</span><input type="number" min="0" max="23" value={load.earliest_hour} onChange={(event) => updateFlexibleLoad(index, { earliest_hour: Number(event.target.value) })} /></label>
            <label><span>Latest</span><input type="number" min="0" max="23" value={load.latest_hour} onChange={(event) => updateFlexibleLoad(index, { latest_hour: Number(event.target.value) })} /></label>
            <button className="icon-button danger-hover" type="button" onClick={() => removeFlexibleLoad(index)} aria-label={`Remove ${load.name}`}><Trash2 size={15} /></button>
          </div>
        ))}
        <button className="button button-add-note" type="button" onClick={addFlexibleLoad} disabled={(scenario.flexible_loads?.length ?? 0) >= 12}>
          <Plus size={15} /> Add flexible task
        </button>
      </div>

      <div className="section-divider table-divider"><span>Hourly operating profile</span><span className="divider-line" /><span className="table-badge">24 intervals</span></div>
      <div className="table-scroll">
        <table className="hours-table">
          <thead>
            <tr><th>Hour</th><th>Demand <small>kWh</small></th><th>Solar <small>kWh</small></th><th>Tariff <small>BDT/kWh</small></th></tr>
          </thead>
          <tbody>
            {scenario.hours.map((row, index) => (
              <tr key={row.hour}>
                <td><span className="hour-chip">{String(row.hour).padStart(2, "0")}:00</span></td>
                <td><input aria-label={`Demand hour ${row.hour}`} type="number" step="0.1" value={row.demand_kwh} onChange={(event) => updateHour(index, "demand_kwh", event.target.value)} /></td>
                <td><input aria-label={`Solar hour ${row.hour}`} type="number" step="0.1" value={row.solar_kwh} onChange={(event) => updateHour(index, "solar_kwh", event.target.value)} /></td>
                <td><input aria-label={`Tariff hour ${row.hour}`} type="number" step="0.01" value={row.tariff_bdt_per_kwh} onChange={(event) => updateHour(index, "tariff_bdt_per_kwh", event.target.value)} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
