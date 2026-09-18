export type HourInput = {
  hour: number;
  demand_kwh: number;
  solar_kwh: number;
  tariff_bdt_per_kwh: number;
};

export type BatteryConfig = {
  capacity_kwh: number;
  initial_energy_kwh: number;
  minimum_energy_kwh: number;
  max_charge_kwh_per_hour: number;
  max_discharge_kwh_per_hour: number;
};

export type Scenario = {
  scenario_id: string;
  operator_notes: string[];
  hours: HourInput[];
  battery: BatteryConfig;
};

export type DirectiveInterpretation = {
  note_index: number;
  applies: boolean;
  directive_type: string;
  structured_adjustment: Record<string, unknown> | null;
  explanation: string;
};

export type HourlyPlan = {
  hour: number;
  grid_kwh: number;
  solar_used_kwh: number;
  battery_action: "charge" | "discharge" | "idle" | string;
  battery_kwh?: number;
  battery_energy_after_kwh?: number;
  cost_bdt?: number;
};

export type SystemStatus = {
  healthy: boolean;
  model_availability: { configured: boolean; models: Array<{ provider: string; model: string; circuit_open: boolean }> };
  optimizer_status: { available: boolean; solver: string };
  monitoring: Record<string, unknown>;
};

export type OptimizeResponse = {
  directive_interpretation: DirectiveInterpretation[];
  hourly_plan: HourlyPlan[];
  total_grid_kwh: number;
  total_cost_bdt: number;
  peak_grid_kwh: number;
  plan_summary: string;
};
