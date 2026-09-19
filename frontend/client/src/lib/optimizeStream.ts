import type { Scenario } from "@/types";

export type StageMap = Record<number, "running" | "completed">;

const HEADERS = { "Content-Type": "application/json" };

async function detailOf(response: Response): Promise<string> {
  const raw = await response.text();
  try {
    const payload: unknown = raw ? JSON.parse(raw) : null;
    if (payload && typeof payload === "object" && "detail" in payload) return String((payload as { detail: unknown }).detail);
  } catch {
    /* fall through */
  }
  return `Request failed with status ${response.status}.`;
}

async function plainOptimize(apiBase: string, body: string): Promise<unknown> {
  const response = await fetch(`${apiBase}/optimize-energy`, { method: "POST", headers: HEADERS, body });
  const raw = await response.text();
  let payload: unknown = null;
  try {
    payload = raw ? JSON.parse(raw) : null;
  } catch {
    throw new Error("The API returned malformed JSON.");
  }
  if (!response.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload ? String((payload as { detail: unknown }).detail) : `Request failed with status ${response.status}.`;
    throw new Error(detail);
  }
  return payload;
}

/** Runs an optimization, reporting real pipeline stages when the API supports streaming. */
export async function optimizeWithProgress(apiBase: string, scenario: Scenario, onStage: (stage: number, state: "running" | "completed") => void): Promise<unknown> {
  const body = JSON.stringify(scenario);
  let response: Response | null = null;
  try {
    response = await fetch(`${apiBase}/optimize-energy/stream`, { method: "POST", headers: HEADERS, body });
  } catch {
    response = null;
  }
  if (!response || response.status === 404 || response.status === 405 || !response.body) return plainOptimize(apiBase, body);
  if (!response.ok) throw new Error(await detailOf(response));

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: unknown;

  const handle = (line: string) => {
    if (!line.trim()) return;
    let event: { event?: string; stage?: number; state?: string; data?: unknown; detail?: string };
    try {
      event = JSON.parse(line);
    } catch {
      return;
    }
    if (event.event === "progress" && typeof event.stage === "number" && (event.state === "running" || event.state === "completed")) onStage(event.stage, event.state);
    else if (event.event === "result") result = event.data;
    else if (event.event === "error") throw new Error(event.detail || "Optimization failed.");
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    lines.forEach(handle);
  }
  handle(buffer);
  if (result === undefined) throw new Error("The optimization stream ended without a result.");
  return result;
}
