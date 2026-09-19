import { motion } from "framer-motion";
import { BatteryCharging, BatteryWarning, CalendarClock, ShieldCheck, Sun, TrendingDown, Zap } from "lucide-react";
import { useEffect, useState } from "react";
import type { Scenario, ScheduledAction } from "@/types";

const META: Record<string, { label: string; icon: React.ReactNode; tone: string }> = {
  BATTERY_CHARGE: { label: "Charge Battery", icon: <BatteryCharging size={17} />, tone: "blue" },
  BATTERY_DISCHARGE: { label: "Peak Shaving", icon: <Zap size={17} />, tone: "orange" },
  SOLAR_PRIORITY: { label: "Solar Priority", icon: <Sun size={17} />, tone: "mint" },
  GRID_REDUCTION: { label: "Reduce Grid Dependency", icon: <TrendingDown size={17} />, tone: "violet" },
  BATTERY_PROTECTION: { label: "Maintain Minimum Reserve", icon: <ShieldCheck size={17} />, tone: "orange" },
};

export function ActionSchedule({ apiBase, scenario, refreshKey }: { apiBase: string; scenario: Scenario; refreshKey: unknown }) {
  const [actions, setActions] = useState<ScheduledAction[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setActions(null);
    setFailed(false);
    fetch(`${apiBase}/scheduler/actions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(scenario) })
      .then((response) => (response.ok ? response.json() : Promise.reject(new Error("bad status"))))
      .then((data: { daily_actions?: ScheduledAction[] }) => { if (!cancelled) setActions(data.daily_actions ?? []); })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
    // Re-run only when a new optimization result arrives, not on every scenario edit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey, apiBase]);

  return (
    <section className="glass-card action-panel" id="action-schedule">
      <div className="panel-heading compact-heading">
        <div><div className="eyebrow"><span className="eyebrow-dot mint" /> Operator playbook</div><h2>AI Action Schedule</h2><p>What to do, when, and why.</p></div>
        {actions && <span className="result-count">{actions.length} actions</span>}
      </div>
      {failed && <div className="plan-empty"><BatteryWarning size={15} /> Couldn't build the action schedule. Try running the optimization again.</div>}
      {!failed && actions === null && <div className="loading-stack"><div className="loading-bar" /><div className="loading-bar" /></div>}
      {actions && actions.length === 0 && <div className="plan-empty"><CalendarClock size={15} /> No special actions needed. The plan runs on autopilot.</div>}
      {actions && actions.length > 0 && (
        <ol className="timeline">
          {actions.map((action, index) => {
            const base = META[action.type] ?? { label: action.type, icon: <Zap size={17} />, tone: "blue" };
            const meta = action.type === "BATTERY_DISCHARGE" && action.priority !== "HIGH" ? { ...base, label: "Discharge Battery", tone: "blue" } : base;
            return (
              <motion.li key={`${action.type}-${action.start_time}`} className={`timeline-item tone-${meta.tone}`} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: index * 0.05 }}>
                <div className="timeline-icon">{meta.icon}</div>
                <div className="timeline-body">
                  <div className="timeline-top"><span className="timeline-time">{action.start_time}–{action.end_time}</span><span className={`priority priority-${action.priority.toLowerCase()}`}>{action.priority}</span></div>
                  <strong>{meta.label}</strong>
                  <p><b>Reason:</b> {action.reason}</p>
                  <p><b>Impact:</b> {action.expected_impact}</p>
                </div>
              </motion.li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
