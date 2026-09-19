import type { ScheduledAction } from "@/types";

export const ACTION_LABELS: Record<string, string> = {
  BATTERY_CHARGE: "Charge Battery",
  BATTERY_DISCHARGE: "Discharge Battery",
  SOLAR_PRIORITY: "Solar Priority",
  GRID_REDUCTION: "Reduce Grid Dependency",
  BATTERY_PROTECTION: "Maintain Minimum Reserve",
};

const label = (action: ScheduledAction) =>
  action.type === "BATTERY_DISCHARGE" && action.priority === "HIGH" ? "Peak Shaving" : ACTION_LABELS[action.type] ?? action.type;

export function actionsToText(scenarioId: string, actions: ScheduledAction[]): string {
  const lines = actions.map((a) => `${a.start_time}-${a.end_time}  ${label(a)} [${a.priority}]\n    Reason: ${a.reason}\n    Impact: ${a.expected_impact}`);
  return `GridWise action plan - ${scenarioId}\n\n${lines.join("\n\n")}\n`;
}

const csvCell = (value: string) => `"${value.replaceAll('"', '""')}"`;

export function actionsToCsv(actions: ScheduledAction[]): string {
  const rows = actions.map((a) => [a.start_time, a.end_time, label(a), a.priority, a.reason, a.expected_impact].map(csvCell).join(","));
  return ["Start,End,Action,Priority,Reason,Impact", ...rows].join("\r\n");
}

export function downloadFile(filename: string, content: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
