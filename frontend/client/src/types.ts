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
  charge_efficiency?: number;
  discharge_efficiency?: number;
  degradation_cost_bdt_per_kwh?: number;
};

export type FlexibleLoad = {
  name: string;
  energy_kwh: number;
  max_power_kwh_per_hour: number;
  earliest_hour: number;
  latest_hour: number;
};

export type Scenario = {
  scenario_id: string;
  operator_notes: string[];
  hours: HourInput[];
  battery: BatteryConfig;
  flexible_loads?: FlexibleLoad[];
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
  flexible_loads?: Record<string, number>;
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

export type ScheduledAction = {
  type: string;
  start_time: string;
  end_time: string;
  priority: "HIGH" | "MEDIUM" | "LOW" | string;
  reason: string;
  expected_impact: string;
};

export type Reliability = {
  directive_understanding: number | null;
  constraint_validation: number | null;
  optimization_validity: number | null;
  checks_run: number;
};

export type ScenarioAnalysis = {
  directives: DirectiveInterpretation[];
  conflict: {
    feasible: boolean;
    conflicting_note_indices: number[];
    conflicting_directive_types: string[];
    hours: number[];
    summary: string;
    suggestions: string[];
  };
};

export type OutageResult = {
  outage_start_hour: number;
  outage_end_hour: number;
  critical_load_fraction: number;
  fully_served: boolean;
  survival_hours: number;
  outage_duration_hours: number;
  total_critical_load_kwh: number;
  unserved_energy_kwh: number;
  minimum_battery_energy_kwh: number;
  hourly: Array<{
    hour: number;
    critical_load_kwh: number;
    solar_used_kwh: number;
    battery_discharge_kwh: number;
    solar_charge_kwh: number;
    unserved_kwh: number;
    battery_energy_after_kwh: number;
  }>;
};

export type ReplanResult = {
  scenario_id: string;
  current_hour: number;
  current_battery_energy_kwh: number;
  remaining_plan: HourlyPlan[];
  remaining_grid_kwh: number;
  remaining_cost_bdt: number;
  remaining_peak_grid_kwh: number;
};
