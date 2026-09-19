import { CheckCircle2, ShieldCheck } from "lucide-react";
import type { Reliability } from "@/types";

const STEPS = ["Operator Request", "AI Understanding", "Directive Validation", "Optimization", "Schedule Generation", "Human Recommendation"];

export function PipelineFlow({ complete, running }: { complete: boolean; running: boolean }) {
  return (
    <section className="glass-card pipeline-panel">
      <div className="panel-heading compact-heading">
        <div><div className="eyebrow"><span className="eyebrow-dot" /> End to end</div><h2>AI Pipeline</h2></div>
      </div>
      <ol className="pipeline">
        {STEPS.map((step, index) => (
          <li key={step} className={complete ? "is-done" : running ? "is-running" : ""}>
            <span className="pipeline-node">{complete ? <CheckCircle2 size={14} /> : index + 1}</span>
            <span className="pipeline-label">{step}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

export function ReliabilityPanel({ reliability }: { reliability: Reliability | null }) {
  const metrics = [
    { label: "Directive Understanding", hint: "Model agreement", value: reliability?.directive_understanding ?? null },
    { label: "Constraint Validation", hint: "Physics and directive checks passed", value: reliability?.constraint_validation ?? null },
    { label: "Optimization Validity", hint: "Independent schedule verification", value: reliability?.optimization_validity ?? null },
  ];
  return (
    <section className="glass-card reliability-panel">
      <div className="panel-heading compact-heading">
        <div><div className="eyebrow"><span className="eyebrow-dot mint" /> Trust</div><h2>AI Reliability</h2></div>
        <ShieldCheck size={18} className="twin-radar" />
      </div>
      <div className="meter-list">
        {metrics.map((metric) => (
          <div className="meter" key={metric.label}>
            <div className="meter-top"><span>{metric.label}</span><strong>{metric.value === null ? "—" : `${metric.value.toFixed(metric.value % 1 ? 1 : 0)}%`}</strong></div>
            <div className="meter-track" role="progressbar" aria-valuenow={metric.value ?? 0} aria-valuemin={0} aria-valuemax={100} aria-label={metric.label} title={metric.hint}><div className="meter-fill" style={{ width: `${metric.value ?? 0}%` }} /></div>
          </div>
        ))}
      </div>
      <p className="meter-note">{reliability ? `Measured from ${reliability.checks_run} independent checks on this plan.` : "Measuring this plan…"}</p>
    </section>
  );
}
