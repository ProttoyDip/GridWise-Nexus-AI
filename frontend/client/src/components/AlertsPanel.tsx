import { AlertTriangle, BellRing, CheckCircle2, Info } from "lucide-react";
import { useEffect, useMemo } from "react";
import { deriveAlerts, setAlertCount } from "@/lib/alerts";
import type { OptimizeResponse, Scenario } from "@/types";
import "@/alerts.css";

const ICONS = { warning: <AlertTriangle size={16} />, info: <Info size={16} />, ok: <CheckCircle2 size={16} /> };

export function AlertsPanel({ scenario, result }: { scenario: Scenario; result: OptimizeResponse }) {
  const alerts = useMemo(() => deriveAlerts(scenario, result, new Date().getHours()), [scenario, result]);
  const warnings = alerts.filter((alert) => alert.level === "warning").length;

  useEffect(() => {
    setAlertCount(warnings);
    return () => setAlertCount(0);
  }, [warnings]);

  return (
    <section className="glass-card alerts-panel" aria-live="polite">
      <div className="panel-heading compact-heading">
        <div><div className="eyebrow"><span className="eyebrow-dot" /> Operator alerts</div><h2>What needs attention</h2></div>
        <span className={`result-count ${warnings ? "alert-count" : ""}`}><BellRing size={11} /> {warnings ? `${warnings} to review` : "All clear"}</span>
      </div>
      <ul className="alert-list">
        {alerts.map((alert) => (
          <li key={alert.id} className={`alert-item alert-${alert.level}`}>
            <span className="alert-icon">{ICONS[alert.level]}</span>
            <span className="alert-body"><strong>{alert.title}</strong><small>{alert.detail}</small></span>
          </li>
        ))}
      </ul>
    </section>
  );
}
