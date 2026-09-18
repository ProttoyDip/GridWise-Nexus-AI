import { Brain, Cpu, Radar } from "lucide-react";
import { useEffect, useState } from "react";
import type { SystemStatus } from "@/types";

type IndicatorState = "online" | "degraded" | "offline" | "checking";

function Indicator({ icon, label, state, detail }: { icon: React.ReactNode; label: string; state: IndicatorState; detail: string }) {
  return (
    <div className={`status-indicator status-${state}`}>
      <div className="status-indicator-icon">{icon}</div>
      <div className="status-indicator-copy">
        <span className="status-indicator-label"><span className="status-dot" /> {label}</span>
        <small>{detail}</small>
      </div>
    </div>
  );
}

export function SystemStatusBar({ apiBase }: { apiBase: string }) {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [state, setState] = useState<"checking" | "ok" | "error">("checking");

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const response = await fetch(`${apiBase}/system/status`, { signal: AbortSignal.timeout(4000) });
        const data = (await response.json()) as SystemStatus;
        if (!cancelled) {
          setStatus(data);
          setState(response.ok ? "ok" : "error");
        }
      } catch {
        if (!cancelled) setState("error");
      }
    };
    poll();
    const timer = window.setInterval(poll, 12000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [apiBase]);

  const llmModels = status?.model_availability.models ?? [];
  const activeModels = llmModels.filter((model) => !model.circuit_open).length;
  const llmState: IndicatorState = state === "checking" ? "checking" : !status?.model_availability.configured ? "offline" : activeModels === 0 ? "offline" : activeModels < llmModels.length ? "degraded" : "online";
  const llmDetail = state === "checking" ? "Checking…" : !status?.model_availability.configured ? "No provider configured" : `${activeModels}/${llmModels.length} models available`;

  const optimizerState: IndicatorState = state === "checking" ? "checking" : status?.optimizer_status.available ? "online" : "offline";
  const optimizerDetail = state === "checking" ? "Checking…" : status?.optimizer_status.available ? `${status.optimizer_status.solver} solver ready` : "Solver unavailable";

  const simState: IndicatorState = state === "checking" ? "checking" : state === "ok" ? "online" : "offline";
  const simDetail = state === "checking" ? "Checking…" : state === "ok" ? "Digital twin ready" : "API unreachable";

  return (
    <div className="status-bar glass-card">
      <Indicator icon={<Brain size={16} />} label="LLM System" state={llmState} detail={llmDetail} />
      <Indicator icon={<Cpu size={16} />} label="Optimizer" state={optimizerState} detail={optimizerDetail} />
      <Indicator icon={<Radar size={16} />} label="Grid Simulation" state={simState} detail={simDetail} />
    </div>
  );
}
