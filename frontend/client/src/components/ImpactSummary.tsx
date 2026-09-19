import { Coins, Leaf, PlugZap, Sun } from "lucide-react";
import { useMemo } from "react";
import { computeImpact, GRID_EMISSION_KG_PER_KWH } from "@/lib/impact";
import type { OptimizeResponse, Scenario } from "@/types";

const fmt = (value: number, digits = 0) => value.toLocaleString(undefined, { maximumFractionDigits: digits });

export function ImpactSummary({ scenario, result }: { scenario: Scenario; result: OptimizeResponse }) {
  const impact = useMemo(() => computeImpact(scenario, result), [scenario, result]);
  const tiles = [
    { icon: <Coins size={16} />, tone: "blue", label: "Cost saved", value: `${fmt(impact.savedBdt)} BDT`, meta: `${impact.savedPct.toFixed(1)}% vs. grid-only` },
    { icon: <PlugZap size={16} />, tone: "violet", label: "Grid energy avoided", value: `${fmt(impact.gridAvoidedKwh)} kWh`, meta: "solar + battery" },
    { icon: <Leaf size={16} />, tone: "mint", label: "CO₂ avoided (est.)", value: `${fmt(impact.co2AvoidedKg)} kg`, meta: `${GRID_EMISSION_KG_PER_KWH} kg/kWh grid factor` },
    { icon: <Sun size={16} />, tone: "orange", label: "Solar utilised", value: `${impact.solarUsedPct.toFixed(0)}%`, meta: "of forecast generation" },
  ];
  return (
    <section className="glass-card impact-panel">
      <div className="panel-heading compact-heading">
        <div><div className="eyebrow"><span className="eyebrow-dot mint" /> Business impact</div><h2>Savings &amp; carbon</h2><p>Compared with buying every kWh from the grid, with no solar or battery.</p></div>
      </div>
      <div className="impact-grid">
        {tiles.map((tile) => (
          <div className={`impact-tile tone-${tile.tone}`} key={tile.label}>
            <span className="impact-icon">{tile.icon}</span>
            <span className="stat-label">{tile.label}</span>
            <strong>{tile.value}</strong>
            <small>{tile.meta}</small>
          </div>
        ))}
      </div>
    </section>
  );
}
