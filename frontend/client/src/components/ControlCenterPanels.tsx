import { CheckCircle2, ShieldCheck } from "lucide-react";

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

const METRICS = [
  { label: "Directive Understanding", value: 96 },
  { label: "Constraint Validation", value: 100 },
  { label: "Optimization Validity", value: 100 },
];

export function ReliabilityPanel() {
  return (
    <section className="glass-card reliability-panel">
      <div className="panel-heading compact-heading">
        <div><div className="eyebrow"><span className="eyebrow-dot mint" /> Trust</div><h2>AI Reliability</h2></div>
        <ShieldCheck size={18} className="twin-radar" />
      </div>
      <div className="meter-list">
        {METRICS.map((metric) => (
          <div className="meter" key={metric.label}>
            <div className="meter-top"><span>{metric.label}</span><strong>{metric.value}%</strong></div>
            <div className="meter-track" role="progressbar" aria-valuenow={metric.value} aria-valuemin={0} aria-valuemax={100} aria-label={metric.label}><div className="meter-fill" style={{ width: `${metric.value}%` }} /></div>
          </div>
        ))}
      </div>
    </section>
  );
}
