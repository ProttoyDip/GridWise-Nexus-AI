import { CheckCircle2, Loader2, ShieldCheck, SlidersHorizontal, Sparkles, XCircle } from "lucide-react";

type AgentState = "idle" | "running" | "complete" | "failed";

const AGENTS: Array<{ key: string; label: string; icon: React.ReactNode }> = [
  { key: "interpreter", label: "Directive Interpreter", icon: <Sparkles size={14} /> },
  { key: "safety", label: "Safety Validator", icon: <ShieldCheck size={14} /> },
  { key: "optimizer", label: "Dispatch Optimizer", icon: <SlidersHorizontal size={14} /> },
  { key: "explain", label: "Explainability Engine", icon: <CheckCircle2 size={14} /> },
];

function AgentRow({ label, icon, state }: { label: string; icon: React.ReactNode; state: AgentState }) {
  return (
    <div className={`agent-row agent-${state}`}>
      <div className="agent-icon">{icon}</div>
      <span className="agent-label">{label}</span>
      <span className="agent-state">
        {state === "running" ? <Loader2 size={13} className="spin" /> : state === "complete" ? <CheckCircle2 size={13} /> : state === "failed" ? <XCircle size={13} /> : <span className="reasoning-step-dot" />}
        {state === "running" ? "Running" : state === "complete" ? "Complete" : state === "failed" ? "Failed" : "Idle"}
      </span>
    </div>
  );
}

export function AgentStatusPanel({ loading, hasResult, hasError }: { loading: boolean; hasResult: boolean; hasError: boolean }) {
  const overall: AgentState = loading ? "running" : hasError ? "failed" : hasResult ? "complete" : "idle";

  return (
    <section className="glass-card agent-panel">
      <div className="panel-heading compact-heading">
        <div><div className="eyebrow"><span className="eyebrow-dot" /> Pipeline</div><h2>AI Agent Status</h2></div>
      </div>
      <div className="agent-list">
        {AGENTS.map((agent) => (
          <AgentRow key={agent.key} label={agent.label} icon={agent.icon} state={overall} />
        ))}
      </div>
    </section>
  );
}
