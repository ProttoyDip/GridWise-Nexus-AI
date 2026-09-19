import { CheckCircle2, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

// `stage` is the backend pipeline stage whose completion finishes each step.
const STEPS = [
  { label: "Reading operator notes", stage: 1 },
  { label: "Checking safety constraints", stage: 3 },
  { label: "Running battery + grid optimization", stage: 4 },
  { label: "Verifying the schedule", stage: 5 },
];

export function ReasoningTimeline({ active, stages }: { active: boolean; stages?: Record<number, string> }) {
  const [step, setStep] = useState(0);
  const live = Boolean(stages && Object.keys(stages).length > 0);

  useEffect(() => {
    if (!active || live) {
      setStep(0);
      return;
    }
    const timer = window.setInterval(() => {
      setStep((current) => (current < STEPS.length - 1 ? current + 1 : current));
    }, 850);
    return () => window.clearInterval(timer);
  }, [active, live]);

  if (!active) return null;

  const current = live ? STEPS.findIndex((item) => stages?.[item.stage] !== "completed") : step;

  return (
    <div className="reasoning-timeline">
      {STEPS.map((item, index) => {
        const done = live ? stages?.[item.stage] === "completed" : index < step;
        const isActive = index === current && !done;
        return (
          <div key={item.label} className={`reasoning-step ${done ? "is-done" : isActive ? "is-active" : ""}`}>
            {done ? <CheckCircle2 size={14} /> : isActive ? <Loader2 size={14} className="spin" /> : <span className="reasoning-step-dot" />}
            <span>{item.label}</span>
          </div>
        );
      })}
    </div>
  );
}
