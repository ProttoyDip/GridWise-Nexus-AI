import { BatteryCharging, Sun, Zap } from "lucide-react";
import { useMemo } from "react";
import type { Scenario } from "@/types";

const fmt = (value: number, digits = 0) => value.toLocaleString(undefined, { maximumFractionDigits: digits });

/** At-a-glance view of the scenario being stress-tested, shown beside the Twin and Outage tools. */
export function ScenarioSnapshot({ scenario }: { scenario: Scenario }) {
  const stats = useMemo(() => {
    const hours = [...scenario.hours].sort((a, b) => a.hour - b.hour);
    const demand = hours.reduce((sum, h) => sum + h.demand_kwh, 0);
    const solar = hours.reduce((sum, h) => sum + h.solar_kwh, 0);
    const tariffs = hours.map((h) => h.tariff_bdt_per_kwh);
    const maxDemand = Math.max(1, ...hours.map((h) => Math.max(h.demand_kwh, h.solar_kwh)));
    const maxTariff = Math.max(1e-9, ...tariffs);
    return { hours, demand, solar, minTariff: Math.min(...tariffs), maxTariff, chartMax: maxDemand, tariffScale: maxTariff };
  }, [scenario]);

  const W = 288;
  const H = 96;
  const barW = W / 24;
  const y = (value: number) => H - (value / stats.chartMax) * (H - 6);
  const tariffPath = stats.hours.map((h, i) => `${i === 0 ? "M" : "L"}${(i + 0.5) * barW},${H - (h.tariff_bdt_per_kwh / stats.tariffScale) * (H - 6)}`).join(" ");

  return (
    <aside className="glass-card snapshot-panel" aria-label="Scenario snapshot">
      <div className="panel-heading compact-heading">
        <div><div className="eyebrow"><span className="eyebrow-dot" /> Scenario snapshot</div><h2>{scenario.scenario_id}</h2></div>
      </div>
      <div className="snapshot-stats">
        <div><span className="snapshot-icon"><Zap size={14} /></span><small>Daily demand</small><strong>{fmt(stats.demand)} kWh</strong></div>
        <div><span className="snapshot-icon snapshot-mint"><Sun size={14} /></span><small>Forecast solar</small><strong>{fmt(stats.solar)} kWh</strong></div>
        <div><span className="snapshot-icon snapshot-violet"><BatteryCharging size={14} /></span><small>Battery</small><strong>{fmt(scenario.battery.capacity_kwh)} kWh</strong></div>
        <div><span className="snapshot-icon snapshot-orange"><Zap size={14} /></span><small>Tariff range</small><strong>{fmt(stats.minTariff, 1)}–{fmt(stats.maxTariff, 1)} BDT</strong></div>
      </div>
      <svg className="snapshot-chart" viewBox={`0 0 ${W} ${H + 14}`} role="img" aria-label="24-hour demand, solar and tariff profile">
        {stats.hours.map((h, i) => (
          <g key={h.hour}>
            <rect x={i * barW + 1} y={y(h.demand_kwh)} width={barW - 2} height={H - y(h.demand_kwh)} rx="1.5" className="snap-demand" />
            <rect x={i * barW + 1} y={y(h.solar_kwh)} width={barW - 2} height={H - y(h.solar_kwh)} rx="1.5" className="snap-solar" />
          </g>
        ))}
        <path d={tariffPath} className="snap-tariff" fill="none" />
        {[0, 6, 12, 18, 23].map((hour) => <text key={hour} x={(hour + 0.5) * barW} y={H + 11} textAnchor="middle" className="snap-label">{String(hour).padStart(2, "0")}</text>)}
      </svg>
      <div className="snapshot-legend"><span><i className="snap-demand" /> Demand</span><span><i className="snap-solar" /> Solar</span><span><i className="snap-tariff-key" /> Tariff</span></div>
      <dl className="snapshot-battery">
        <div><dt>Starts at</dt><dd>{fmt(scenario.battery.initial_energy_kwh)} kWh</dd></div>
        <div><dt>Reserve</dt><dd>{fmt(scenario.battery.minimum_energy_kwh)} kWh</dd></div>
        <div><dt>Max charge</dt><dd>{fmt(scenario.battery.max_charge_kwh_per_hour)} kWh/h</dd></div>
        <div><dt>Max discharge</dt><dd>{fmt(scenario.battery.max_discharge_kwh_per_hour)} kWh/h</dd></div>
      </dl>
    </aside>
  );
}
