import { AlertTriangle, CheckCircle2, Eye, Loader2, Pencil, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import type { Scenario, ScenarioAnalysis } from "@/types";

const directiveLabels: Record<string, string> = {
  solar_reduction: "Solar reduction",
  minimum_battery_reserve: "Battery reserve",
  no_charge_window: "No charging",
  no_discharge_window: "No discharging",
  max_grid_window: "Grid import limit",
  no_op: "No operational change",
};

function adjustmentCopy(adjustment: Record<string, unknown> | null): string {
  if (!adjustment) return "No structured constraint";
  const hours = Array.isArray(adjustment.hours)
    ? `Hours ${(adjustment.hours as number[]).map((hour) => `${String(hour).padStart(2, "0")}:00`).join(", ")}`
    : "";
  const value = Object.entries(adjustment)
    .filter(([key]) => key !== "hours")
    .map(([key, item]) => `${key.replaceAll("_", " ")}: ${String(item)}`)
    .join(" · ");
  return [hours, value].filter(Boolean).join(" · ");
}

export function InstructionReview({
  apiBase,
  scenario,
  disabled = false,
}: {
  apiBase: string;
  scenario: Scenario;
  disabled?: boolean;
}) {
  const [analysis, setAnalysis] = useState<ScenarioAnalysis | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setAnalysis(null);
    setError(null);
  }, [scenario]);

  const analyze = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`${apiBase}/analyze-scenario`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(scenario),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail || `Analysis failed (${response.status})`);
      setAnalysis(payload as ScenarioAnalysis);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not analyze this scenario.");
    } finally {
      setLoading(false);
    }
  };

  const editNote = (index: number) => {
    const input = document.getElementById(`operator-note-${index}`) as HTMLInputElement | null;
    input?.focus();
    input?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  return (
    <section className="glass-card instruction-review" aria-live="polite">
      <div className="instruction-review-head">
        <div>
          <div className="eyebrow"><span className="eyebrow-dot" /> Instruction check</div>
          <h2>Preview the operating constraints</h2>
          <p>Confirm how each note will be applied and catch conflicts before running the plan.</p>
        </div>
        <button className="button button-outline" type="button" onClick={analyze} disabled={disabled || loading}>
          {loading ? <Loader2 className="spin" size={15} /> : analysis ? <RefreshCw size={15} /> : <Eye size={15} />}
          {loading ? "Analyzing…" : analysis ? "Analyze again" : "Analyze instructions"}
        </button>
      </div>

      {error && <div className="instruction-analysis-error"><AlertTriangle size={15} /><span>{error}</span></div>}

      {analysis && (
        <div className="instruction-analysis-body">
          <div className={`feasibility-banner ${analysis.conflict.feasible ? "is-feasible" : "is-conflicted"}`}>
            {analysis.conflict.feasible ? <CheckCircle2 size={17} /> : <AlertTriangle size={17} />}
            <div><strong>{analysis.conflict.feasible ? "Constraints are jointly feasible" : "Constraint conflict found"}</strong><span>{analysis.conflict.summary}</span></div>
          </div>

          <div className="directive-preview-list">
            {analysis.directives.map((directive) => {
              const conflicted = analysis.conflict.conflicting_note_indices.includes(directive.note_index);
              return (
                <article className={`directive-preview ${conflicted ? "is-conflicted" : ""}`} key={directive.note_index}>
                  <span className="directive-preview-number">{directive.note_index + 1}</span>
                  <div className="directive-preview-copy">
                    <strong>{directiveLabels[directive.directive_type] || directive.directive_type.replaceAll("_", " ")}</strong>
                    <span>{adjustmentCopy(directive.structured_adjustment)}</span>
                    <small>{directive.explanation}</small>
                  </div>
                  <button className="button button-quiet" type="button" onClick={() => editNote(directive.note_index)}>
                    <Pencil size={13} /> Edit note
                  </button>
                </article>
              );
            })}
          </div>

          {!analysis.conflict.feasible && analysis.conflict.suggestions.length > 0 && (
            <div className="conflict-suggestions">
              <strong>Ways to resolve it</strong>
              <ul>{analysis.conflict.suggestions.map((suggestion) => <li key={suggestion}>{suggestion}</li>)}</ul>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
