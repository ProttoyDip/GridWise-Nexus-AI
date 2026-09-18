import { motion } from "framer-motion";
import { AlertTriangle, CheckCircle2, Loader2, Play, Radar, XCircle } from "lucide-react";
import { useRef, useState } from "react";
import type { Scenario } from "@/types";
import type { SimulationOutcome, SimulationVisualData } from "@/components/copilot/types";

const PRESETS = [
  "What if solar output drops by 50% tomorrow?",
  "What happens if demand spikes by 30% during peak hours?",
  "Simulate the battery being 40% unavailable all day.",
];

type RunState = "idle" | "loading" | "ready" | "error";

export function DigitalTwinTab({ apiBase, scenario }: { apiBase: string; scenario: Scenario }) {
  const [prompt, setPrompt] = useState(PRESETS[0]);
  const [state, setState] = useState<RunState>("idle");
  const [outcomes, setOutcomes] = useState<SimulationOutcome[]>([]);
  const [reply, setReply] = useState("");
  const sessionIdRef = useRef(`twin-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`);

  const runSimulation = async (message: string) => {
    setState("loading");
    setOutcomes([]);
    try {
      const response = await fetch(`${apiBase}/copilot/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionIdRef.current,
          message,
          context: { scenario_id: scenario.scenario_id, hours: scenario.hours, battery: scenario.battery },
        }),
        signal: AbortSignal.timeout(60_000),
      });
      if (!response.ok) throw new Error(`Simulation request failed (${response.status})`);
      const data = await response.json();
      setReply(data.reply ?? "");
      const visual = data.visual_data as SimulationVisualData | null;
      setOutcomes(visual?.kind === "simulation_result" ? visual.outcomes : []);
      setState("ready");
    } catch {
      setState("error");
    }
  };

  return (
    <section className="glass-card twin-panel">
      <div className="panel-heading compact-heading">
        <div><div className="eyebrow"><span className="eyebrow-dot mint" /> Digital twin</div><h2>Stress-test this scenario</h2><p>Runs the real GridWise simulator against your current hours, battery, and directives — no fabricated outcomes.</p></div>
        <Radar size={20} className="twin-radar" />
      </div>

      <div className="twin-presets">
        {PRESETS.map((preset) => (
          <button key={preset} type="button" className={`button button-outline ${prompt === preset ? "is-active" : ""}`} onClick={() => setPrompt(preset)}>{preset}</button>
        ))}
      </div>

      <div className="twin-input-row">
        <div className="input-shell input-shell-wide">
          <input aria-label="What-if scenario" value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="Describe a what-if condition…" />
        </div>
        <button type="button" className="button button-primary" disabled={state === "loading" || !prompt.trim()} onClick={() => runSimulation(prompt)}>
          {state === "loading" ? <><Loader2 size={15} className="spin" /> Simulating…</> : <><Play size={14} fill="currentColor" /> Run simulation</>}
        </button>
      </div>

      {state === "error" && <div className="error-panel" style={{ marginTop: 14 }}><div className="error-icon"><AlertTriangle size={17} /></div><div><strong>Simulation unavailable</strong><p>Couldn't reach the digital twin service.</p></div></div>}

      {state === "ready" && (
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="twin-results">
          {reply && <p className="twin-reply">{reply}</p>}
          <div className="twin-outcomes">
            {outcomes.map((outcome, index) => (
              <div key={`${outcome.name}-${index}`} className={`twin-outcome ${outcome.feasible ? "is-feasible" : "is-infeasible"}`}>
                {outcome.feasible ? <CheckCircle2 size={15} /> : <XCircle size={15} />}
                <div>
                  <strong>{outcome.name}</strong>
                  <small>{outcome.assumption}</small>
                  {outcome.feasible ? (
                    <small className="twin-outcome-metrics">{outcome.cost_bdt?.toLocaleString()} BDT · {outcome.grid_kwh?.toLocaleString()} kWh grid</small>
                  ) : (
                    <small className="twin-outcome-metrics">Infeasible{outcome.error_type ? ` — ${outcome.error_type}` : ""}</small>
                  )}
                </div>
              </div>
            ))}
            {outcomes.length === 0 && <p className="twin-empty">No structured outcomes returned — see the summary above.</p>}
          </div>
        </motion.div>
      )}
    </section>
  );
}
