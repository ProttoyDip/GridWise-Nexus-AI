// Shared types for the GridWise AI Energy Copilot chat UI.
// Mirrors backend/app/api/copilot.py's CopilotChatResponse exactly —
// this file has no independent logic, only shapes.

export type CopilotIntent =
  | "OPTIMIZATION_REQUEST"
  | "EXPLANATION_REQUEST"
  | "SIMULATION_REQUEST"
  | "STATUS_REQUEST"
  | "GENERAL_ENERGY_QUERY";

export interface DirectiveCard {
  type: "directive";
  title: string;
  directive_type: string;
  time_window: string | null;
  status: string;
  explanation: string;
}

export interface OptimizationVisualData {
  kind: "optimization_result";
  cost_bdt: number;
  grid_kwh: number;
  peak_grid_kwh: number;
  charge_window: string | null;
  discharge_window: string | null;
  directive_cards: DirectiveCard[];
  plan_summary: string;
}

export interface ExplanationVisualData {
  kind: "explanation";
  why_this_strategy: string[];
  cost_explanation?: string | null;
}

export interface SimulationOutcome {
  name: string;
  assumption: string;
  feasible: boolean;
  cost_bdt?: number;
  grid_kwh?: number;
  error_type?: string;
}

export interface SimulationVisualData {
  kind: "simulation_result";
  outcomes: SimulationOutcome[];
  risk: Record<string, unknown>;
}

export interface StatusVisualData {
  kind: "system_status";
  healthy: boolean;
  active_models: number;
  total_models: number;
  optimizer_available: boolean;
}

export type VisualData =
  | OptimizationVisualData
  | ExplanationVisualData
  | SimulationVisualData
  | StatusVisualData
  | null;

export interface ChatMessageData {
  id: string;
  role: "user" | "assistant";
  text: string;
  intent?: CopilotIntent;
  actionTaken?: string;
  visualData?: VisualData;
  timestamp: number;
  isError?: boolean;
}

export interface CopilotChatResponse {
  reply: string;
  intent: CopilotIntent;
  action_taken: string;
  visual_data: VisualData;
}

export type CopilotStatus = "idle" | "processing" | "error";
