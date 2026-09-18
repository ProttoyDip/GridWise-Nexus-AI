import { AnimatePresence, motion } from "framer-motion";
import {
  Braces,
  ChevronDown,
  Copy,
  FileJson,
  Plus,
  RotateCcw,
  Sparkles,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { useState } from "react";
import type { BatteryConfig, Scenario } from "@/types";

type ScenarioBuilderProps = {
  scenario: Scenario;
  onChange: (next: Scenario) => void;
  onRandomize: () => void;
  onLoadJson: (value: string) => void;
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
];

export function ScenarioBuilder({
  scenario,
  onChange,
  onRandomize,
  onLoadJson,
  sampleJson,
}: ScenarioBuilderProps) {
  const [sampleOpen, setSampleOpen] = useState(false);
  const [jsonValue, setJsonValue] = useState(sampleJson);
  const [copied, setCopied] = useState(false);

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

  const copyJson = async () => {
    await navigator.clipboard?.writeText(JSON.stringify(scenario, null, 2));
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
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
          <button className="button button-quiet" type="button" onClick={copyJson} title="Copy scenario JSON">
            {copied ? <Sparkles size={15} /> : <Copy size={15} />}
            {copied ? "Copied" : "Copy JSON"}
          </button>
          <button className="button button-quiet" type="button" onClick={onRandomize}>
            <RotateCcw size={15} /> Randomize
          </button>
        </div>
      </div>

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
        <div className="json-loader-wrap">
          <button
            className={`button button-outline ${sampleOpen ? "is-active" : ""}`}
            type="button"
            onClick={() => setSampleOpen((open) => !open)}
          >
            <FileJson size={16} /> Load sample case <ChevronDown size={15} className={sampleOpen ? "rotate-180" : ""} />
          </button>
          <AnimatePresence initial={false}>
            {sampleOpen && (
              <motion.div
                className="json-loader"
                initial={{ opacity: 0, y: -8, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: -8, scale: 0.98 }}
              >
                <div className="json-loader-topline">
                  <span><Braces size={14} /> Paste a scenario object</span>
                  <button type="button" className="icon-button" onClick={() => setSampleOpen(false)} aria-label="Close JSON loader"><X size={15} /></button>
                </div>
                <textarea value={jsonValue} onChange={(event) => setJsonValue(event.target.value)} spellCheck={false} />
                <button type="button" className="button button-primary button-full" onClick={() => { onLoadJson(jsonValue); setSampleOpen(false); }}>
                  <Upload size={15} /> Apply to builder
                </button>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>

      <div className="section-divider"><span>Operator notes</span><span className="divider-line" /></div>
      <div className="notes-stack">
        {scenario.operator_notes.map((note, index) => (
          <div className="note-row" key={`note-${index}`}>
            <div className="note-index">0{index + 1}</div>
            <input
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
              <input type="number" step="0.1" value={scenario.battery[key]} onChange={(event) => updateBattery(key, event.target.value)} />
              <span className="input-suffix">{suffix}</span>
            </div>
          </label>
        ))}
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
