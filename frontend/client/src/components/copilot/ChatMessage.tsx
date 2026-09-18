import { motion } from "framer-motion";
import { AlertTriangle, BatteryCharging, CheckCircle2, Gauge, ShieldCheck, TrendingDown } from "lucide-react";
import type {
  ChatMessageData,
  ExplanationVisualData,
  OptimizationVisualData,
  SimulationVisualData,
  StatusVisualData,
} from "./types";

// Minimal, dependency-free markdown-ish formatting: **bold**, and lines
// starting with a bullet/check character rendered as a list item. This
// intentionally does not pull in a full markdown renderer — the Copilot's
// replies are short, structured lines (see response_generator.py), not
// arbitrary markdown documents.
function FormattedText({ text }: { text: string }) {
  const lines = text.split("\n");
  return (
    <div className="space-y-1">
      {lines.map((line, index) => {
        const isBullet = /^[•✓\-]\s?/.test(line);
        const content = line.replace(/^[•✓\-]\s?/, "");
        const parts = content.split(/(\*\*[^*]+\*\*)/g).filter(Boolean);
        const rendered = parts.map((part, partIndex) =>
          part.startsWith("**") && part.endsWith("**") ? (
            <strong key={partIndex} className="font-semibold text-cyan-200">
              {part.slice(2, -2)}
            </strong>
          ) : (
            <span key={partIndex}>{part}</span>
          ),
        );
        return isBullet ? (
          <div key={index} className="flex items-start gap-1.5">
            <CheckCircle2 size={13} className="mt-0.5 shrink-0 text-emerald-400" />
            <span>{rendered}</span>
          </div>
        ) : (
          <p key={index}>{rendered}</p>
        );
      })}
    </div>
  );
}

function StatPill({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-white/10 bg-black/20 px-2 py-1.5">
      <div className="text-[10px] uppercase tracking-wide text-slate-400">{label}</div>
      <div className="text-sm font-semibold text-slate-100">{value}</div>
    </div>
  );
}

function OptimizationCard({ data }: { data: OptimizationVisualData }) {
  return (
    <div className="mt-2 space-y-2 rounded-xl border border-emerald-400/20 bg-gradient-to-br from-emerald-500/10 to-cyan-500/5 p-3 backdrop-blur-md">
      <div className="flex items-center gap-1.5 text-sm font-semibold text-emerald-300">
        <CheckCircle2 size={15} /> Optimization Complete
      </div>
      <div className="grid grid-cols-3 gap-2">
        <StatPill label="Cost" value={`${data.cost_bdt.toLocaleString(undefined, { maximumFractionDigits: 0 })} BDT`} />
        <StatPill label="Grid Usage" value={`${data.grid_kwh.toLocaleString(undefined, { maximumFractionDigits: 0 })} kWh`} />
        <StatPill label="Peak" value={`${data.peak_grid_kwh.toLocaleString(undefined, { maximumFractionDigits: 0 })} kWh`} />
      </div>
      {(data.charge_window || data.discharge_window) && (
        <div className="flex items-center gap-2 rounded-md border border-white/10 bg-black/20 px-2.5 py-1.5 text-xs text-slate-300">
          <BatteryCharging size={14} className="text-cyan-300" />
          {data.charge_window && <span>Charge {data.charge_window}</span>}
          {data.charge_window && data.discharge_window && <span className="text-slate-600">|</span>}
          {data.discharge_window && <span>Discharge {data.discharge_window}</span>}
        </div>
      )}
      {data.directive_cards.length > 0 && (
        <div className="space-y-1.5 pt-1">
          <div className="text-[10px] uppercase tracking-wide text-slate-400">AI Understanding</div>
          {data.directive_cards.map((card, index) => (
            <div key={index} className="rounded-md border border-white/10 bg-black/20 px-2.5 py-1.5 text-xs">
              <div className="flex items-center justify-between">
                <span className="font-medium text-slate-100">{card.title}</span>
                <span className="flex items-center gap-1 text-emerald-400">
                  <ShieldCheck size={12} /> {card.status}
                </span>
              </div>
              {card.time_window && <div className="text-slate-400">Time: {card.time_window}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ExplanationCard({ data }: { data: ExplanationVisualData }) {
  return (
    <div className="mt-2 space-y-1.5 rounded-xl border border-cyan-400/20 bg-cyan-500/5 p-3 backdrop-blur-md">
      <div className="text-sm font-semibold text-cyan-300">Why This Strategy?</div>
      {data.why_this_strategy.map((point, index) => (
        <div key={index} className="flex items-start gap-1.5 text-xs text-slate-300">
          <CheckCircle2 size={13} className="mt-0.5 shrink-0 text-emerald-400" />
          <span>{point}</span>
        </div>
      ))}
    </div>
  );
}

function SimulationCard({ data }: { data: SimulationVisualData }) {
  const risk = data.risk as { recommendation?: string };
  return (
    <div className="mt-2 space-y-2 rounded-xl border border-amber-400/20 bg-amber-500/5 p-3 backdrop-blur-md">
      <div className="flex items-center gap-1.5 text-sm font-semibold text-amber-300">
        <TrendingDown size={15} /> Simulation (Hypothetical)
      </div>
      <div className="space-y-1.5">
        {data.outcomes
          .filter((o) => o.name !== "nominal")
          .map((outcome, index) => (
            <div key={index} className="rounded-md border border-white/10 bg-black/20 px-2.5 py-1.5 text-xs">
              <div className="text-slate-300">{outcome.assumption}</div>
              {outcome.feasible ? (
                <div className="text-slate-100">
                  {outcome.cost_bdt?.toLocaleString(undefined, { maximumFractionDigits: 0 })} BDT ·{" "}
                  {outcome.grid_kwh?.toLocaleString(undefined, { maximumFractionDigits: 0 })} kWh
                </div>
              ) : (
                <div className="flex items-center gap-1 text-rose-400">
                  <AlertTriangle size={12} /> Infeasible
                </div>
              )}
            </div>
          ))}
      </div>
      {risk?.recommendation && <div className="text-xs italic text-slate-400">{risk.recommendation}</div>}
    </div>
  );
}

function StatusCard({ data }: { data: StatusVisualData }) {
  return (
    <div className="mt-2 flex items-center gap-3 rounded-xl border border-white/10 bg-white/5 p-3 backdrop-blur-md">
      <Gauge size={20} className={data.healthy ? "text-emerald-400" : "text-amber-400"} />
      <div className="text-xs text-slate-300">
        <div className="font-semibold text-slate-100">{data.healthy ? "System Healthy" : "Degraded"}</div>
        <div>
          {data.active_models}/{data.total_models} models active · Optimizer{" "}
          {data.optimizer_available ? "available" : "unavailable"}
        </div>
      </div>
    </div>
  );
}

function VisualCard({ message }: { message: ChatMessageData }) {
  const data = message.visualData;
  if (!data) return null;
  switch (data.kind) {
    case "optimization_result":
      return <OptimizationCard data={data} />;
    case "explanation":
      return <ExplanationCard data={data} />;
    case "simulation_result":
      return <SimulationCard data={data} />;
    case "system_status":
      return <StatusCard data={data} />;
    default:
      return null;
  }
}

interface ChatMessageProps {
  message: ChatMessageData;
}

export default function ChatMessage({ message }: ChatMessageProps) {
  const isUser = message.role === "user";
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.22, ease: "easeOut" }}
      className={`flex ${isUser ? "justify-end" : "justify-start"}`}
    >
      <div
        className={`max-w-[85%] rounded-2xl px-3.5 py-2.5 text-[13px] leading-relaxed shadow-lg ${
          isUser
            ? "rounded-br-sm bg-gradient-to-br from-cyan-500 to-blue-600 text-white"
            : message.isError
              ? "rounded-bl-sm border border-rose-500/30 bg-rose-950/40 text-rose-100"
              : "rounded-bl-sm border border-white/10 bg-white/[0.06] text-slate-100 backdrop-blur-md"
        }`}
      >
        <FormattedText text={message.text} />
        {!isUser && <VisualCard message={message} />}
      </div>
    </motion.div>
  );
}
