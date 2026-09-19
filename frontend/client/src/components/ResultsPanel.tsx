import { AnimatePresence, motion } from "framer-motion";
import { Activity, ArrowDownRight, ArrowUpRight, BatteryCharging, CircleDashed, CloudSun, Gauge, Zap } from "lucide-react";
import { Area, AreaChart, Bar, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { BatteryConfig, DirectiveInterpretation, HourlyPlan, OptimizeResponse } from "@/types";

type ResultsPanelProps = { result: OptimizeResponse | null; loading: boolean; battery?: BatteryConfig };

function formatAdjustmentKey(key: string) {
  return key.replaceAll("_", " ").replace(/\bkwh\b/i, "kWh").replace(/^./, (c) => c.toUpperCase());
}

function formatAdjustmentValue(value: unknown): string {
  if (Array.isArray(value)) {
    if (value.every((entry) => typeof entry === "number")) {
      return value.map((hour) => `${String(hour).padStart(2, "0")}:00`).join(", ");
    }
    return value.join(", ");
  }
  if (typeof value === "number") return value.toLocaleString();
  return String(value);
}

function AnimatedValue({ value, decimals = 1 }: { value: number; decimals?: number }) {
  return <motion.span key={value} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.28 }}>{value.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}</motion.span>;
}

function actionColor(action: string) {
  if (action === "charge") return "#55e6bc";
  if (action === "discharge") return "#ff9c6e";
  return "#70809d";
}

function ActionBadge({ action }: { action: string }) {
  const normalized = action.toLowerCase();
  const isCharge = normalized === "charge";
  const isDischarge = normalized === "discharge";
  return <span className={`action-badge ${isCharge ? "action-charge" : isDischarge ? "action-discharge" : "action-idle"}`}>
    {isCharge ? <ArrowUpRight size={13} /> : isDischarge ? <ArrowDownRight size={13} /> : <CircleDashed size={12} />}{action}
  </span>;
}

function DirectiveCard({ item, index }: { item: DirectiveInterpretation; index: number }) {
  return <motion.article className="directive-card" initial={{ opacity: 0, x: 14 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: index * 0.06, duration: 0.28 }}>
    <div className="directive-topline">
      <span className="directive-index">NOTE {String(item.note_index + 1).padStart(2, "0")}</span>
      <span className={`applies-badge ${item.applies ? "applies-yes" : "applies-no"}`}><span className="status-dot" />{item.applies ? "Applied" : "Not applied"}</span>
    </div>
    <div className="directive-title-row"><h3>{(item.directive_type || "Unclassified directive").replaceAll("_", " ")}</h3><Activity size={15} /></div>
    <p>{item.explanation || "No explanation returned for this directive."}</p>
    {item.structured_adjustment && Object.keys(item.structured_adjustment).length > 0 && (
      <dl className="adjustment-facts">
        {Object.entries(item.structured_adjustment).map(([key, value]) => (
          <div className="adjustment-fact" key={key}><dt>{formatAdjustmentKey(key)}</dt><dd>{formatAdjustmentValue(value)}</dd></div>
        ))}
      </dl>
    )}
  </motion.article>;
}

function PlanTooltip({ active, payload, label }: { active?: boolean; payload?: Array<{ dataKey?: string; value?: number; color?: string }>; label?: string }) {
  if (!active || !payload?.length) return null;
  const labelFor: Record<string, string> = { grid_kwh: "Grid", solar_used_kwh: "Solar used", battery_energy_after_kwh: "Battery SoC" };
  return <div className="chart-tooltip"><div className="tooltip-label">{String(label).padStart(2, "0")}:00 dispatch</div>{payload.filter((entry) => entry.dataKey !== "actionIndex" && entry.dataKey && entry.dataKey in labelFor).map((entry) => <div className="tooltip-row" key={entry.dataKey}><span style={{ background: entry.color }} />{labelFor[entry.dataKey as string]}<strong>{Number(entry.value).toFixed(1)} kWh</strong></div>)}</div>;
}

export function ResultsPanel({ result, loading, battery }: ResultsPanelProps) {
  const plan: HourlyPlan[] = result?.hourly_plan ?? [];
  const chartData = plan.map((item) => ({ ...item, actionIndex: item.battery_action === "charge" ? 1 : item.battery_action === "discharge" ? -1 : 0 }));
  const hasSoc = plan.some((item) => typeof item.battery_energy_after_kwh === "number");

  return <div className="results-stack">
    <section className="glass-card directive-panel">
      <div className="panel-heading compact-heading">
        <div><div className="eyebrow"><span className="eyebrow-dot violet" /> Directive interpretation</div><h2>Operator intent, decoded</h2></div>
        {result && <span className="result-count">{result.directive_interpretation.length} signals</span>}
      </div>
      <AnimatePresence mode="wait">
        {loading ? <div className="loading-stack" key="loading"><div className="loading-bar wide" /><div className="loading-bar" /><div className="loading-bar short" /></div> : result ? <div className="directive-list" key="result">{result.directive_interpretation.map((item, index) => <DirectiveCard item={item} index={index} key={`${item.note_index}-${index}`} />)}</div> : <div className="empty-result" key="empty"><div className="empty-orbit"><Zap size={20} /></div><h3>Ready to optimize</h3><p>Run the scenario to see how the model translates operator notes into dispatch actions.</p><div className="empty-rule" /></div>}
      </AnimatePresence>
    </section>

    <section className="glass-card plan-panel">
      <div className="panel-heading compact-heading">
        <div><div className="eyebrow"><span className="eyebrow-dot mint" /> Optimization output</div><h2>24-hour dispatch plan</h2></div>
        {result && <span className="live-tag"><span className="status-dot" /> plan ready</span>}
      </div>
      {result ? <>
        <div className="stats-grid">
          <div className="stat-card"><div className="stat-icon blue"><Zap size={16} /></div><span className="stat-label">Total grid draw</span><strong><AnimatedValue value={result.total_grid_kwh} /> <small>kWh</small></strong><span className="stat-meta">across 24 intervals</span></div>
          <div className="stat-card"><div className="stat-icon violet"><Gauge size={16} /></div><span className="stat-label">Total cost</span><strong><AnimatedValue value={result.total_cost_bdt} /> <small>BDT</small></strong><span className="stat-meta">optimized tariff path</span></div>
          <div className="stat-card"><div className="stat-icon orange"><ArrowUpRight size={16} /></div><span className="stat-label">Peak grid load</span><strong><AnimatedValue value={result.peak_grid_kwh} /> <small>kWh</small></strong><span className="stat-meta">single-hour maximum</span></div>
        </div>
        <div className="chart-wrap">
          <div className="chart-legend"><span><i className="legend-swatch grid" /> Grid draw</span><span><i className="legend-swatch solar" /> Solar used</span><span><i className="legend-swatch battery" /> Battery action</span></div>
          <ResponsiveContainer width="100%" height={270}>
            <ComposedChart data={chartData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
              <defs><linearGradient id="gridFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#6b8cff" stopOpacity={0.9} /><stop offset="100%" stopColor="#6b8cff" stopOpacity={0.18} /></linearGradient><linearGradient id="solarFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#55e6bc" stopOpacity={0.9} /><stop offset="100%" stopColor="#55e6bc" stopOpacity={0.12} /></linearGradient></defs>
              <CartesianGrid stroke="#273149" strokeDasharray="3 5" vertical={false} />
              <XAxis dataKey="hour" tick={{ fill: "#687796", fontSize: 11 }} tickLine={false} axisLine={false} tickFormatter={(value) => `${String(value).padStart(2, "0")}h`} interval={2} />
              <YAxis tick={{ fill: "#687796", fontSize: 11 }} tickLine={false} axisLine={false} width={42} />
              <Tooltip content={<PlanTooltip />} cursor={{ fill: "rgba(107, 140, 255, 0.05)" }} />
              <Bar dataKey="grid_kwh" name="Grid draw" fill="url(#gridFill)" radius={[4, 4, 0, 0]} barSize={13} />
              <Bar dataKey="solar_used_kwh" name="Solar used" fill="url(#solarFill)" radius={[4, 4, 0, 0]} barSize={13} />
              <Line type="monotone" dataKey="actionIndex" name="Battery action" stroke="#ff9c6e" strokeWidth={2} dot={({ cx, cy, payload }) => <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r={3.5} fill={actionColor(payload.battery_action)} stroke="#101522" strokeWidth={2} />} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
        {hasSoc && (
          <div className="chart-wrap">
            <div className="chart-legend"><span><i className="legend-swatch battery-soc" /> Battery state of charge</span>{battery && <span><i className="legend-swatch reserve" /> Minimum reserve</span>}</div>
            <ResponsiveContainer width="100%" height={150}>
              <AreaChart data={chartData} margin={{ top: 6, right: 10, left: -20, bottom: 0 }}>
                <defs><linearGradient id="socFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#c293ff" stopOpacity={0.55} /><stop offset="100%" stopColor="#c293ff" stopOpacity={0.04} /></linearGradient></defs>
                <CartesianGrid stroke="#273149" strokeDasharray="3 5" vertical={false} />
                <XAxis dataKey="hour" tick={{ fill: "#687796", fontSize: 11 }} tickLine={false} axisLine={false} tickFormatter={(value) => `${String(value).padStart(2, "0")}h`} interval={2} />
                <YAxis tick={{ fill: "#687796", fontSize: 11 }} tickLine={false} axisLine={false} width={42} />
                <Tooltip content={<PlanTooltip />} cursor={{ fill: "rgba(194, 147, 255, 0.05)" }} />
                {battery && <ReferenceLine y={battery.minimum_energy_kwh} stroke="#ff9c6e" strokeDasharray="4 4" strokeWidth={1.5} />}
                <Area type="monotone" dataKey="battery_energy_after_kwh" name="Battery SoC" stroke="#c293ff" strokeWidth={2} fill="url(#socFill)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
        <div className="plan-footer"><div className="summary-icon"><CloudSun size={17} /></div><div><span className="stat-label">Plan summary</span><p>{result.plan_summary || "No summary returned."}</p></div></div>
        <div className="plan-table-wrap"><div className="plan-table-header"><span>Dispatch detail</span><span>Battery action by interval</span></div><div className="plan-strip">{plan.map((item) => <div className="plan-strip-item" key={item.hour} title={`${item.hour}:00 — ${item.battery_action}`}><span>{String(item.hour).padStart(2, "0")}</span><i style={{ background: actionColor(item.battery_action), boxShadow: `0 0 10px ${actionColor(item.battery_action)}66` }} /></div>)}</div></div>
      </> : <div className="plan-empty"><BatteryCharging size={22} /><span>Plan metrics and dispatch curve will appear here after a successful run.</span></div>}
    </section>
  </div>;
}
