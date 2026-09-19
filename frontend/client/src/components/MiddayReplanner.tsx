import { BatteryMedium, Check, Clock3, Loader2, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import type { OptimizeResponse, ReplanResult, Scenario } from "@/types";

export function MiddayReplanner({
  apiBase,
  scenario,
  result,
  onApply,
}: {
  apiBase: string;
  scenario: Scenario;
  result: OptimizeResponse;
  onApply: (replan: ReplanResult) => void;
}) {
  const initialHour = Math.min(23, new Date().getHours());
  const [currentHour, setCurrentHour] = useState(initialHour);
  const [batteryEnergy, setBatteryEnergy] = useState(scenario.battery.initial_energy_kwh);
  const [replan, setReplan] = useState<ReplanResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [applied, setApplied] = useState(false);

  useEffect(() => {
    const previous = result.hourly_plan.find((entry) => entry.hour === currentHour - 1);
    setBatteryEnergy(previous?.battery_energy_after_kwh ?? scenario.battery.initial_energy_kwh);
    setReplan(null);
    setApplied(false);
  }, [currentHour, result, scenario.battery.initial_energy_kwh]);

  const run = async () => {
    setLoading(true);
    setError(null);
    setReplan(null);
    setApplied(false);
    try {
      const response = await fetch(`${apiBase}/replan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scenario,
          directives: result.directive_interpretation,
          current_hour: currentHour,
          current_battery_energy_kwh: batteryEnergy,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail || `Replanning failed (${response.status})`);
      setReplan(payload as ReplanResult);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not create the remaining-day plan.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="glass-card replan-panel">
      <div className="replan-copy">
        <div className="eyebrow"><span className="eyebrow-dot violet" /> Midday replanning</div>
        <h2>Update the rest of today</h2>
        <p>Enter the observed battery energy. Completed hours stay fixed while future hours are optimized again.</p>
      </div>
      <div className="replan-controls">
        <label className="field-label"><span><Clock3 size={12} /> Current hour</span><div className="input-shell"><input type="number" min={0} max={23} value={currentHour} onChange={(event) => setCurrentHour(Number(event.target.value))} /><span className="input-suffix">:00</span></div></label>
        <label className="field-label"><span><BatteryMedium size={12} /> Measured energy</span><div className="input-shell"><input type="number" min={scenario.battery.minimum_energy_kwh} max={scenario.battery.capacity_kwh} step="0.1" value={batteryEnergy} onChange={(event) => setBatteryEnergy(Number(event.target.value))} /><span className="input-suffix">kWh</span></div></label>
        <button className="button button-outline" type="button" onClick={run} disabled={loading}>
          {loading ? <Loader2 className="spin" size={15} /> : <RefreshCw size={15} />}{loading ? "Replanning…" : "Build remaining plan"}
        </button>
      </div>
      {error && <div className="instruction-analysis-error"><span>{error}</span></div>}
      {replan && (
        <div className="replan-result">
          <div><span>Remaining cost</span><strong>{replan.remaining_cost_bdt.toLocaleString(undefined, { maximumFractionDigits: 0 })} BDT</strong></div>
          <div><span>Remaining grid</span><strong>{replan.remaining_grid_kwh.toLocaleString(undefined, { maximumFractionDigits: 1 })} kWh</strong></div>
          <div><span>Future peak</span><strong>{replan.remaining_peak_grid_kwh.toLocaleString(undefined, { maximumFractionDigits: 1 })} kWh</strong></div>
          <button className="button button-primary" type="button" disabled={applied} onClick={() => { onApply(replan); setApplied(true); }}>
            <Check size={15} /> {applied ? "Plan adopted" : "Adopt remaining plan"}
          </button>
        </div>
      )}
    </section>
  );
}
