import { AlertTriangle, BatteryCharging, CheckCircle2, Loader2, Shield, Zap } from "lucide-react";
import { useState } from "react";
import type { OutageResult, Scenario } from "@/types";

const fmt = (value: number) => value.toLocaleString(undefined, { maximumFractionDigits: 1 });

export function OutagePlanner({ apiBase, scenario }: { apiBase: string; scenario: Scenario }) {
  const [startHour, setStartHour] = useState(18);
  const [endHour, setEndHour] = useState(23);
  const [criticalPercent, setCriticalPercent] = useState(50);
  const [result, setResult] = useState<OutageResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const response = await fetch(`${apiBase}/resilience/outage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scenario,
          start_hour: startHour,
          end_hour: endHour,
          critical_load_fraction: criticalPercent / 100,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail || `Outage analysis failed (${response.status})`);
      setResult(payload as OutageResult);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not evaluate the outage window.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="glass-card outage-planner">
      <div className="panel-heading">
        <div>
          <div className="eyebrow"><span className="eyebrow-dot violet" /> Resilience planner</div>
          <h2>Protect essential loads during an outage</h2>
          <p>Test a grid-loss window against the current solar forecast, battery reserve, and critical-load share.</p>
        </div>
      </div>

      <div className="outage-controls">
        <label className="field-label"><span>Outage starts</span><div className="input-shell"><input type="number" min={0} max={23} value={startHour} onChange={(event) => setStartHour(Number(event.target.value))} /><span className="input-suffix">hour</span></div></label>
        <label className="field-label"><span>Outage ends</span><div className="input-shell"><input type="number" min={0} max={23} value={endHour} onChange={(event) => setEndHour(Number(event.target.value))} /><span className="input-suffix">hour</span></div></label>
        <label className="field-label"><span>Essential load</span><div className="input-shell"><input type="number" min={1} max={100} value={criticalPercent} onChange={(event) => setCriticalPercent(Number(event.target.value))} /><span className="input-suffix">%</span></div></label>
        <button className="button button-primary" type="button" onClick={run} disabled={loading || endHour < startHour}>
          {loading ? <Loader2 className="spin" size={16} /> : <Shield size={16} />}{loading ? "Evaluating…" : "Evaluate resilience"}
        </button>
      </div>

      {error && <div className="instruction-analysis-error"><AlertTriangle size={15} /><span>{error}</span></div>}

      {result && (
        <div className="outage-results" aria-live="polite">
          <div className={`outage-verdict ${result.fully_served ? "is-feasible" : "is-shortfall"}`}>
            {result.fully_served ? <CheckCircle2 size={20} /> : <AlertTriangle size={20} />}
            <div><strong>{result.fully_served ? "Essential load covered" : "Energy shortfall detected"}</strong><span>{result.survival_hours} of {result.outage_duration_hours} hours fully served</span></div>
          </div>
          <div className="outage-metrics">
            <div><Shield size={16} /><span>Critical energy</span><strong>{fmt(result.total_critical_load_kwh)} kWh</strong></div>
            <div><Zap size={16} /><span>Unserved energy</span><strong>{fmt(result.unserved_energy_kwh)} kWh</strong></div>
            <div><BatteryCharging size={16} /><span>Lowest battery</span><strong>{fmt(result.minimum_battery_energy_kwh)} kWh</strong></div>
          </div>
          <div className="outage-hour-strip" aria-label="Hourly outage coverage">
            {result.hourly.map((row) => {
              const covered = row.unserved_kwh <= 0.000001;
              return <div className={covered ? "is-covered" : "is-unserved"} key={row.hour} title={`${fmt(row.unserved_kwh)} kWh unserved`}><span>{String(row.hour).padStart(2, "0")}:00</span><strong>{covered ? "Covered" : `${fmt(row.unserved_kwh)} kWh short`}</strong></div>;
            })}
          </div>
        </div>
      )}
    </section>
  );
}
