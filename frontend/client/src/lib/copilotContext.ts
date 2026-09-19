import type { Scenario } from "@/types";

// The copilot answers about whatever scenario is on the dashboard, so it never contradicts the screen.
let current: Record<string, unknown> | null = null;

export function setCopilotContext(scenario: Scenario | null) {
  current = scenario ? { scenario_id: scenario.scenario_id, hours: scenario.hours, battery: scenario.battery } : null;
}

export function getCopilotContext(): Record<string, unknown> | null {
  return current;
}
