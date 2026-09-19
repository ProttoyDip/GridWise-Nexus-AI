import { AlertTriangle, BellRing, CalendarClock, CheckCircle2, Info } from "lucide-react";
import { hourCoverage, type AlertKind, type OpAlert } from "@/lib/alerts";

const ICONS = { warning: <AlertTriangle size={16} />, info: <Info size={16} />, ok: <CheckCircle2 size={16} /> };
const KIND_LABEL: Record<AlertKind, string> = { reserve: "Battery at reserve", peak: "Peak tariff", solar: "Solar curtailed", grid: "Peak grid import", clear: "" };
const LEGEND: AlertKind[] = ["reserve", "peak", "solar", "grid"];

function AlertList({ alerts, onShowSchedule }: { alerts: OpAlert[]; onShowSchedule: () => void }) {
  return (
    <ul className="alert-list">
      {alerts.map((alert) => (
        <li key={alert.id} className={`alert-item alert-${alert.level}`}>
          <span className="alert-icon">{ICONS[alert.level]}</span>
          <span className="alert-body">
            <strong>{alert.title}</strong>
            <small>{alert.detail}</small>
            {alert.window && (
              <button type="button" className="alert-link" onClick={onShowSchedule}><CalendarClock size={12} /> See the action schedule</button>
            )}
          </span>
        </li>
      ))}
    </ul>
  );
}

export function AlertsTab({ alerts, hasResult, onShowSchedule, onGoToDashboard }: { alerts: OpAlert[]; hasResult: boolean; onShowSchedule: () => void; onGoToDashboard: () => void }) {
  if (!hasResult) {
    return (
      <section className="glass-card alerts-panel alerts-empty" aria-live="polite">
        <span className="alerts-empty-icon"><BellRing size={22} /></span>
        <h2>No alerts yet</h2>
        <p>Run an optimization and GridWise will flag battery reserve limits, peak-tariff windows, curtailed solar, and peak grid import here.</p>
        <button type="button" className="button button-outline" onClick={onGoToDashboard}>Go to the dashboard</button>
      </section>
    );
  }

  const needs = alerts.filter((alert) => alert.level === "warning");
  const notices = alerts.filter((alert) => alert.level !== "warning");
  const coverage = hourCoverage(alerts);

  return (
    <div className="alerts-layout" aria-live="polite">
      <section className="glass-card alerts-panel alerts-overview">
        <div className="panel-heading compact-heading">
          <div><div className="eyebrow"><span className="eyebrow-dot" /> Operator alerts</div><h2>What needs attention</h2><p>Derived from the plan you just ran, not from a separate model.</p></div>
          <span className={`result-count ${needs.length ? "alert-count" : ""}`}><BellRing size={11} /> {needs.length ? `${needs.length} to review` : "All clear"}</span>
        </div>
        <div className="risk-strip" role="img" aria-label="24-hour alert coverage">
          {coverage.map((kinds, hour) => (
            <div key={hour} className="risk-cell" title={kinds.length ? `${String(hour).padStart(2, "0")}:00 — ${kinds.map((k) => KIND_LABEL[k]).join(", ")}` : `${String(hour).padStart(2, "0")}:00 — no alerts`}>
              <span className="risk-bars">{kinds.map((kind) => <i key={kind} className={`risk-${kind}`} />)}</span>
              <small>{hour % 3 === 0 ? String(hour).padStart(2, "0") : ""}</small>
            </div>
          ))}
        </div>
        <div className="risk-legend">{LEGEND.map((kind) => <span key={kind}><i className={`risk-${kind}`} /> {KIND_LABEL[kind]}</span>)}</div>
      </section>

      <section className="glass-card alerts-panel">
        <div className="panel-heading compact-heading"><div><div className="eyebrow"><span className="eyebrow-dot violet" /> Review</div><h2>Needs attention</h2></div><span className="result-count">{needs.length}</span></div>
        {needs.length ? <AlertList alerts={needs} onShowSchedule={onShowSchedule} /> : <p className="alerts-none"><CheckCircle2 size={15} /> Nothing needs review. The plan is within battery, solar and grid limits.</p>}
      </section>

      <section className="glass-card alerts-panel">
        <div className="panel-heading compact-heading"><div><div className="eyebrow"><span className="eyebrow-dot mint" /> For awareness</div><h2>Good to know</h2></div><span className="result-count">{notices.length}</span></div>
        {notices.length ? <AlertList alerts={notices} onShowSchedule={onShowSchedule} /> : <p className="alerts-none">No additional notices.</p>}
      </section>
    </div>
  );
}
