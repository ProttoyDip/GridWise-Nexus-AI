import { motion } from "framer-motion";
import { ArrowRight, Scale, TrendingDown } from "lucide-react";
import { useEffect, useState } from "react";
import type { OptimizeResponse, Scenario } from "@/types";

type BaselineState = "idle" | "loading" | "ready" | "error";

function isOptimizeResponse(value: unknown): value is OptimizeResponse {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<OptimizeResponse>;
  return Array.isArray(item.hourly_plan) && typeof item.total_cost_bdt === "number";
}

export function BeforeAfterComparison({ apiBase, scenario, result }: { apiBase: string; scenario: Scenario; result: OptimizeResponse }) {
  const [baseline, setBaseline] = useState<OptimizeResponse | null>(null);
  const [state, setState] = useState<BaselineState>("idle");

  // Resets to idle whenever a new result comes in, so a stale baseline
  // from a previous scenario is never shown next to the new result —
  // comparing runs a second /optimize-energy call (LLM + solver), so it
  // only happens when the operator explicitly asks for it below.
  useEffect(() => {
    setState("idle");
    setBaseline(null);
  }, [result]);

  const runComparison = async () => {
    setState("loading");
    try {
      const neutralScenario: Scenario = { ...scenario, operator_notes: ["No special operating instructions for this window."] };
      const response = await fetch(`${apiBase}/optimize-energy`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(neutralScenario),
      });
      const payload: unknown = await response.json();
      if (response.ok && isOptimizeResponse(payload)) {
        setBaseline(payload);
        setState("ready");
      } else {
        setState("error");
      }
    } catch {
      setState("error");
    }
  };

  const costDelta = baseline ? result.total_cost_bdt - baseline.total_cost_bdt : 0;
  const gridDelta = baseline ? result.total_grid_kwh - baseline.total_grid_kwh : 0;
  const savingsPct = baseline && baseline.total_cost_bdt > 0 ? (-costDelta / baseline.total_cost_bdt) * 100 : 0;

  return (
    <section className="glass-card comparison-panel">
      <div className="panel-heading compact-heading">
        <div><div className="eyebrow"><span className="eyebrow-dot mint" /> Before vs after</div><h2>Operator directives vs a neutral baseline</h2></div>
        {state === "ready" && savingsPct !== 0 && (
          <span className={`live-tag ${savingsPct > 0 ? "" : "applies-no"}`}><TrendingDown size={12} /> {savingsPct > 0 ? `${savingsPct.toFixed(1)}% lower cost` : `${Math.abs(savingsPct).toFixed(1)}% higher cost`}</span>
        )}
      </div>

      {(state === "idle" || state === "error") && (
        <div className="comparison-prompt">
          <p>{state === "error" ? "Couldn't reach the API for a baseline run. Try again." : "Runs one extra optimization with directives cleared, so you can see exactly what they changed."}</p>
          <button type="button" className="button button-outline" onClick={runComparison}>
            <Scale size={14} /> Compare against baseline
          </button>
        </div>
      )}

      {state === "loading" && <div className="loading-stack"><div className="loading-bar wide" /><div className="loading-bar" /></div>}

      {state === "ready" && baseline && (
        <motion.div className="comparison-grid" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
          <div className="comparison-col">
            <span className="stat-label">Baseline (no directives)</span>
            <strong>{baseline.total_cost_bdt.toLocaleString()} <small>BDT</small></strong>
            <small>{baseline.total_grid_kwh.toLocaleString()} kWh grid draw · {baseline.peak_grid_kwh.toLocaleString()} kWh peak</small>
          </div>
          <ArrowRight size={18} className="comparison-arrow" />
          <div className="comparison-col comparison-col-highlight">
            <span className="stat-label">With operator directives</span>
            <strong>{result.total_cost_bdt.toLocaleString()} <small>BDT</small></strong>
            <small>{result.total_grid_kwh.toLocaleString()} kWh grid draw · {result.peak_grid_kwh.toLocaleString()} kWh peak · {gridDelta <= 0 ? "" : "+"}{gridDelta.toFixed(0)} kWh vs baseline</small>
          </div>
        </motion.div>
      )}
    </section>
  );
}
