import { motion } from "framer-motion";
import { AlertTriangle, ArrowRight, Clock3, Cpu, LayoutDashboard, Loader2, Moon, Play, RadarIcon, RefreshCw, ShieldCheck, Sparkles, Sun, Terminal, Zap } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { ActionSchedule, useSchedulerInsights } from "@/components/ActionSchedule";
import { PipelineFlow, ReliabilityPanel } from "@/components/ControlCenterPanels";
import { AgentStatusPanel } from "@/components/AgentStatusPanel";
import { BeforeAfterComparison } from "@/components/BeforeAfterComparison";
import { DigitalTwinTab } from "@/components/DigitalTwinTab";
import { ReasoningTimeline } from "@/components/ReasoningTimeline";
import { ResultsPanel } from "@/components/ResultsPanel";
import { ScenarioBuilder } from "@/components/ScenarioBuilder";
import { SystemStatusBar } from "@/components/SystemStatusBar";
import { WhyAIDecided } from "@/components/WhyAIDecided";
import type { HourInput, OptimizeResponse, Scenario } from "@/types";
import samplePack from "@/data/sampleCases.json";

const OPERATOR_PRESETS = [
  { label: "Solar maintenance", note: "Facilities will wash the rooftop solar panels from noon until 2 PM. Treat usable solar as roughly 25% of forecast during that window." },
  { label: "Battery protection", note: "For protection testing, the battery must not discharge from 6 PM until 8 PM." },
  { label: "Peak demand control", note: "From 6 PM until 9 PM, campus grid import must not exceed the feeder's temporary limit." },
];

const API_BASE = (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");

type SamplePackCase = { id: string; label: string; input: Scenario };
const samplePackCases = (samplePack as { cases: SamplePackCase[] }).cases;

function cloneSampleCase(caseId: string): Scenario {
  const found = samplePackCases.find((entry) => entry.id === caseId) ?? samplePackCases[0];
  return JSON.parse(JSON.stringify(found.input)) as Scenario;
}

function createInitialScenario(): Scenario {
  return cloneSampleCase(samplePackCases[0].id);
}

function createSampleJson(): string {
  return JSON.stringify(createInitialScenario(), null, 2);
}

function randomizedFromPack(): Scenario {
  const pick = samplePackCases[Math.floor(Math.random() * samplePackCases.length)];
  const base = JSON.parse(JSON.stringify(pick.input)) as Scenario;
  const rng = (min: number, max: number) => min + Math.random() * (max - min);
  const hours: HourInput[] = base.hours.map((row) => ({
    ...row,
    demand_kwh: Number(Math.max(0, row.demand_kwh * rng(0.9, 1.1)).toFixed(1)),
    solar_kwh: Number(Math.max(0, row.solar_kwh * rng(0.85, 1.15)).toFixed(1)),
  }));
  return { ...base, scenario_id: `${base.scenario_id} / variant-${new Date().toISOString().slice(11, 19).replaceAll(":", "")}`, hours };
}

function isScenario(value: unknown): value is Scenario {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<Scenario>;
  return typeof item.scenario_id === "string" && Array.isArray(item.operator_notes) && item.operator_notes.length >= 1 && item.operator_notes.length <= 3 && Array.isArray(item.hours) && item.hours.length === 24 && Boolean(item.battery && typeof item.battery === "object");
}

function isOptimizeResponse(value: unknown): value is OptimizeResponse {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<OptimizeResponse>;
  return Array.isArray(item.directive_interpretation) && Array.isArray(item.hourly_plan) && typeof item.plan_summary === "string";
}

export default function Home() {
  const [scenario, setScenario] = useState<Scenario>(() => createInitialScenario());
  const [result, setResult] = useState<OptimizeResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [health, setHealth] = useState<"checking" | "connected" | "disconnected">("checking");
  const [lastHealthCheck, setLastHealthCheck] = useState<Date | null>(null);
  const [activeTab, setActiveTab] = useState<"dashboard" | "twin">("dashboard");
  const insights = useSchedulerInsights(API_BASE, scenario, result);
  const [theme, setTheme] = useState<"dark" | "light">(() => {
    try { return localStorage.getItem("gridwise-theme") === "light" ? "light" : "dark"; } catch { return "dark"; }
  });

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "light" ? "#f4f7fc" : "#0a0d15");
    try { localStorage.setItem("gridwise-theme", theme); } catch { /* storage unavailable */ }
  }, [theme]);

  useEffect(() => {
    let cancelled = false;
    const checkHealth = async () => {
      try {
        const response = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(3200) });
        const data = await response.json();
        if (!cancelled) {
          setHealth(response.ok && data?.status === "ok" ? "connected" : "disconnected");
          setLastHealthCheck(new Date());
        }
      } catch {
        if (!cancelled) {
          setHealth("disconnected");
          setLastHealthCheck(new Date());
        }
      }
    };
    checkHealth();
    const timer = window.setInterval(checkHealth, 6000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  const sampleJson = useMemo(createSampleJson, []);

  const loadSampleCase = (caseId: string) => {
    setScenario(cloneSampleCase(caseId));
    setResult(null);
    setError(null);
  };

  const applyPreset = (note: string) => {
    setScenario((current) => ({ ...current, operator_notes: [note, ...current.operator_notes.slice(1)] }));
    setResult(null);
    setError(null);
  };

  const loadJson = (value: string) => {
    try {
      const parsed: unknown = JSON.parse(value);
      if (!isScenario(parsed)) throw new Error("Expected 24 hourly rows, 1–3 operator notes, and a complete battery object.");
      setScenario(parsed);
      setResult(null);
      setError(null);
    } catch (reason) {
      setError(`Could not load scenario JSON: ${reason instanceof Error ? reason.message : "invalid JSON"}`);
    }
  };

  const handleSubmit = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE}/optimize-energy`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(scenario),
      });
      const raw = await response.text();
      let payload: unknown = null;
      try { payload = raw ? JSON.parse(raw) : null; } catch { throw new Error("The API returned malformed JSON."); }
      if (!response.ok) {
        const detail = payload && typeof payload === "object" && "detail" in payload ? String((payload as { detail: unknown }).detail) : `Request failed with status ${response.status}.`;
        throw new Error(detail);
      }
      if (!isOptimizeResponse(payload)) throw new Error("The API response is missing a directive interpretation or hourly plan.");
      setResult(payload);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to reach the optimization API.");
    } finally {
      setLoading(false);
    }
  };

  const healthCopy = health === "connected" ? "API connected" : health === "disconnected" ? "API unavailable" : "Checking API";

  return <div className="app-shell">
    <div className="ambient ambient-one" /><div className="ambient ambient-two" /><div className="ambient ambient-three" />
    <header className="topbar page-width">
      <div className="brand-lockup"><div className="brand-mark"><Zap size={18} fill="currentColor" /></div><div><div className="brand-name">GridWise <span>Nexus</span></div><div className="brand-subtitle">AI operations center</div></div></div>
      <div className="topbar-right"><div className={`connection-pill ${health}`}><span className="status-dot" />{healthCopy}</div><button className="theme-toggle" type="button" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"} title={theme === "dark" ? "Light mode" : "Dark mode"}>{theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}</button><button className="icon-button top-icon" type="button" title="Refresh connection" onClick={() => window.location.reload()}><RefreshCw size={15} /></button></div>
    </header>

    <main className="page-width main-content">
      <section className="hero-row">
        <div className="hero-copy"><div className="hero-kicker"><span className="kicker-line" /> AI ENERGY OPERATIONS CONTROL CENTER <span className="kicker-line" /></div><h1>Instruct. Optimize. <span>Act with confidence.</span></h1><p>Run high-fidelity energy scenarios against your optimization API. Edit the operating envelope, encode what matters, and inspect every decision.</p></div>
        <div className="hero-health"><div className="health-orbit"><div className="orbit-ring ring-one" /><div className="orbit-ring ring-two" /><div className="orbit-core"><Cpu size={21} /></div></div><div><span>Model interface</span><strong>{health === "connected" ? "Ready for inference" : health === "checking" ? "Checking signal" : "Offline mode"}</strong><small>{lastHealthCheck ? `Last checked ${lastHealthCheck.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : "Polling every 6 sec"}</small></div></div>
      </section>

      <SystemStatusBar apiBase={API_BASE} />

      <div className="tab-strip">
        <button type="button" className={`tab-button ${activeTab === "dashboard" ? "is-active" : ""}`} onClick={() => setActiveTab("dashboard")}><LayoutDashboard size={14} /> Operator Dashboard</button>
        <button type="button" className={`tab-button ${activeTab === "twin" ? "is-active" : ""}`} onClick={() => setActiveTab("twin")}><RadarIcon size={14} /> Digital Twin</button>
      </div>

      {activeTab === "twin" ? (
        <DigitalTwinTab apiBase={API_BASE} scenario={scenario} />
      ) : (
        <>
          <section className="glass-card command-center">
            <div className="panel-heading compact-heading">
              <div><div className="eyebrow"><span className="eyebrow-dot" /> Operator command center</div><h2>Set the operating intent</h2><p>Pick a common directive or write your own in the scenario builder below — the AI interprets it against real constraints.</p></div>
            </div>
            <div className="command-presets">
              {OPERATOR_PRESETS.map((preset) => (
                <button key={preset.label} type="button" className="command-preset" onClick={() => applyPreset(preset.note)}>
                  <span className="command-preset-label">{preset.label}</span>
                  <span className="command-preset-note">{preset.note}</span>
                </button>
              ))}
            </div>
          </section>

          {loading && <div className="glass-card reasoning-panel"><div className="eyebrow"><span className="eyebrow-dot" /> AI reasoning</div><ReasoningTimeline active={loading} /></div>}

          <div className="workspace-grid">
            <div className="builder-column"><ScenarioBuilder scenario={scenario} onChange={setScenario} onRandomize={() => { setScenario(randomizedFromPack()); setResult(null); setError(null); }} onLoadJson={loadJson} onLoadSampleCase={loadSampleCase} sampleJson={sampleJson} /></div>
            <div className="results-column"><ResultsPanel result={result} loading={loading} battery={scenario.battery} /></div>
          </div>

          <div className="submit-bar glass-card"><div className="submit-context"><div className="submit-icon"><ShieldCheck size={18} /></div><div><strong>Ready to run <span>{scenario.scenario_id}</span></strong><small>POST /optimize-energy · {scenario.hours.length} hourly intervals · {scenario.operator_notes.length} operator directives</small></div></div><button className="button button-primary submit-button" type="button" onClick={handleSubmit} disabled={loading}>{loading ? <><Loader2 size={17} className="spin" /> Optimizing…</> : <><Play size={15} fill="currentColor" /> Run optimization <ArrowRight size={16} /></>}</button></div>

          {error && <motion.div className="error-panel" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}><div className="error-icon"><AlertTriangle size={17} /></div><div><strong>Optimization request needs attention</strong><p>{error}</p></div><button type="button" className="icon-button" onClick={() => setError(null)} aria-label="Dismiss error">×</button></motion.div>}

          {result && !loading && (
            <div className="insight-grid">
              <div className="insight-col">
                <ActionSchedule actions={insights.actions} failed={insights.failed} />
                <BeforeAfterComparison apiBase={API_BASE} scenario={scenario} result={result} />
                <WhyAIDecided result={result} />
              </div>
              <div className="insight-col">
                <AgentStatusPanel loading={loading} hasResult={Boolean(result)} hasError={Boolean(error)} />
                <PipelineFlow complete={Boolean(result) && !error} running={loading} />
                <ReliabilityPanel reliability={insights.reliability} />
              </div>
            </div>
          )}
        </>
      )}

      <footer className="footer"><span><Sparkles size={13} /> Built for high-signal energy operations</span><span><Clock3 size={13} /> Session autosaves locally</span><span><Terminal size={13} /> v0.9.4-beta</span></footer>
    </main>
  </div>;
}
