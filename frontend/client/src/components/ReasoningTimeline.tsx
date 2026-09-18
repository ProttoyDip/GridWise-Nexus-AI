import { CheckCircle2, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

const STEPS = [
  "Reading operator notes",
  "Checking safety constraints",
  "Running battery + grid optimization",
  "Building dispatch strategy",
];

export function ReasoningTimeline({ active }: { active: boolean }) {
  const [step, setStep] = useState(0);

  useEffect(() => {
    if (!active) {
      setStep(0);
      return;
    }
    const timer = window.setInterval(() => {
      setStep((current) => (current < STEPS.length - 1 ? current + 1 : current));
    }, 850);
    return () => window.clearInterval(timer);
  }, [active]);

  if (!active) return null;

  return (
    <div className="reasoning-timeline">
      {STEPS.map((label, index) => {
        const done = index < step;
        const isActive = index === step;
        return (
          <div key={label} className={`reasoning-step ${done ? "is-done" : isActive ? "is-active" : ""}`}>
            {done ? <CheckCircle2 size={14} /> : isActive ? <Loader2 size={14} className="spin" /> : <span className="reasoning-step-dot" />}
            <span>{label}</span>
          </div>
        );
      })}
    </div>
  );
}
