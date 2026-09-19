import { CheckCircle2, ShieldCheck } from "lucide-react";
import type { Reliability } from "@/types";

const STEPS = ["Operator Request", "AI Understanding", "Directive Validation", "Optimization", "Schedule Generation", "Human Recommendation"];

export type StepState = "done" | "running" | "idle";

/** Maps real backend stage events (1 interpret, 3 validate, 4 optimize, 5 verify) onto the six visible steps. */
export function pipelineStates(stages: Record<number, string>, ctx: { loading: boolean; hasResult: boolean; scheduled: boolean }): StepState[] {
  const started = ctx.loading || ctx.hasResult;
  const streamed = Object.keys(stages).length > 0;
  const done = (stage: number) => (streamed ? stages[stage] === "completed" : ctx.hasResult);
  const flags = [started, done(1), done(3), done(4), ctx.hasResult && ctx.scheduled && done(5), ctx.hasResult && ctx.scheduled];
  const firstPending = flags.indexOf(false);
  return flags.map((flag, index) => (flag ? "done" : ctx.loading && index === firstPending ? "running" : "idle"));
}

export function PipelineFlow({ states }: { states: StepState[] }) {
  return (
    <section className="glass-card pipeline-panel">
      <div className="panel-heading compact-heading">
        <div><div className="eyebrow"><span className="eyebrow-dot" /> End to end</div><h2>AI Pipeline</h2></div>
      </div>
      <ol className="pipeline">
        {STEPS.map((step, index) => (
          <li key={step} className={states[index] === "done" ? "is-done" : states[index] === "running" ? "is-running" : ""}>
            <span className="pipeline-node">{states[index] === "done" ? <CheckCircle2 size={14} /> : index + 1}</span>
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
