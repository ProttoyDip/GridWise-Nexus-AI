import { History, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { compareRuns, type RunRecord } from "@/lib/history";

const fmt = (value: number, digits: number) => value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });

export function RunHistory({ runs, onClear }: { runs: RunRecord[]; onClear: () => void }) {
  const [picked, setPicked] = useState<string[]>([]);

  useEffect(() => {
    setPicked((current) => current.filter((id) => runs.some((run) => run.id === id)));
  }, [runs]);

  if (runs.length === 0) return null;

  const toggle = (id: string) => setPicked((current) => (current.includes(id) ? current.filter((x) => x !== id) : [...current.slice(-1), id]));
  const [a, b] = picked.map((id) => runs.find((run) => run.id === id));
  const deltas = a && b ? compareRuns(a, b) : null;

  return (
    <section className="glass-card history-panel" id="sec-history">
      <div className="panel-heading compact-heading">
        <div><div className="eyebrow"><span className="eyebrow-dot violet" /> Run history</div><h2>Compare past runs</h2><p>Select any two runs to compare them side by side.</p></div>
        <button type="button" className="button button-quiet" onClick={onClear}><Trash2 size={14} /> Clear</button>
      </div>
      <ul className="history-list">
        {runs.map((run) => {
          const index = picked.indexOf(run.id);
          return (
            <li key={run.id}>
              <label className={`history-row ${index >= 0 ? "is-picked" : ""}`}>
                <input type="checkbox" checked={index >= 0} onChange={() => toggle(run.id)} aria-label={`Select run ${run.scenarioId} at ${new Date(run.at).toLocaleTimeString()}`} />
                <span className="history-badge">{index >= 0 ? (index === 0 ? "A" : "B") : <History size={12} />}</span>
                <span className="history-main"><strong>{run.scenarioId}</strong><small>{run.directive || "No directive"}</small></span>
                <span className="history-meta"><b>{fmt(run.totalCostBdt, 0)} BDT</b><small>{new Date(run.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</small></span>
              </label>
            </li>
          );
        })}
      </ul>
      {deltas && (
        <div className="compare-table" role="table" aria-label="Run comparison">
          <div className="compare-head" role="row"><span /><span>A</span><span>B</span><span>Change</span></div>
          {deltas.map((row) => {
            const diff = row.b - row.a;
            const better = row.lowerIsBetter ? diff < 0 : diff > 0;
            const same = Math.abs(diff) < 10 ** -row.digits / 2;
            return (
              <div className="compare-row" role="row" key={row.label}>
                <span>{row.label}</span>
                <span>{fmt(row.a, row.digits)} {row.unit}</span>
                <span>{fmt(row.b, row.digits)} {row.unit}</span>
                <span className={same ? "" : better ? "delta-good" : "delta-bad"}>{same ? "—" : `${diff > 0 ? "+" : ""}${fmt(diff, row.digits)}`}</span>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
