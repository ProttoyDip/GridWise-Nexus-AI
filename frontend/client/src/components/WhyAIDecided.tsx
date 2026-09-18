import { BatteryCharging, Lightbulb, Sparkles } from "lucide-react";
import type { OptimizeResponse } from "@/types";

export function WhyAIDecided({ result }: { result: OptimizeResponse }) {
  const applied = result.directive_interpretation.filter((item) => item.applies);
  const chargeHours = result.hourly_plan.filter((row) => row.battery_action === "charge").length;
  const dischargeHours = result.hourly_plan.filter((row) => row.battery_action === "discharge").length;
  const cheapestHour = result.hourly_plan.reduce((best, row) => (row.grid_kwh < best.grid_kwh ? row : best), result.hourly_plan[0]);
  const peakHour = result.hourly_plan.reduce((worst, row) => (row.grid_kwh > worst.grid_kwh ? row : worst), result.hourly_plan[0]);

  return (
    <section className="glass-card why-panel">
      <div className="panel-heading compact-heading">
        <div><div className="eyebrow"><span className="eyebrow-dot violet" /> Explainable AI</div><h2>Why AI decided this</h2></div>
      </div>

      <div className="why-summary"><Lightbulb size={16} /><p>{result.plan_summary || "No summary returned for this run."}</p></div>

      {applied.length > 0 && (
        <ul className="why-list">
          {applied.map((item, index) => (
            <li key={`${item.note_index}-${index}`}>
              <Sparkles size={13} />
              <span><strong>{item.directive_type.replaceAll("_", " ")}</strong> — {item.explanation}</span>
            </li>
          ))}
        </ul>
      )}

      <div className="why-battery">
        <BatteryCharging size={15} />
        <span>
          Charged in {chargeHours} of 24 hours and discharged in {dischargeHours}, drawing the least grid power at {String(cheapestHour.hour).padStart(2, "0")}:00
          ({cheapestHour.grid_kwh.toFixed(0)} kWh) and the most at {String(peakHour.hour).padStart(2, "0")}:00 ({peakHour.grid_kwh.toFixed(0)} kWh).
        </span>
      </div>
    </section>
  );
}
