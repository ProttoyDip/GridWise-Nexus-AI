import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatMessageData, CopilotChatResponse, CopilotStatus } from "./types";

// Same env var and fallback Home.tsx already uses for the real GridWise
// API, so the Copilot always talks to the same backend as the rest of
// the dashboard.
const API_BASE = (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");

const SESSION_KEY = "gridwise_copilot_session_id";
const MESSAGES_KEY = "gridwise_copilot_messages";
const MAX_STORED_MESSAGES = 50;

export const PROCESSING_STEPS = [
  "Understanding request",
  "Checking energy constraints",
  "Running optimization",
  "Preparing recommendation",
];

function safeLocalStorage() {
  try {
    const testKey = "__gridwise_copilot_test__";
    window.localStorage.setItem(testKey, "1");
    window.localStorage.removeItem(testKey);
    return window.localStorage;
  } catch {
    return null;
  }
}

function readOrCreateSessionId(): string {
  const storage = safeLocalStorage();
  const existing = storage?.getItem(SESSION_KEY);
  if (existing) return existing;
  const fresh =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `sess-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  storage?.setItem(SESSION_KEY, fresh);
  return fresh;
}

function readStoredMessages(): ChatMessageData[] {
  const storage = safeLocalStorage();
  const raw = storage?.getItem(MESSAGES_KEY);
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as ChatMessageData[]) : [];
  } catch {
    return [];
  }
}

export function useCopilotChat() {
  const sessionIdRef = useRef<string>(readOrCreateSessionId());
  const [messages, setMessages] = useState<ChatMessageData[]>(() => readStoredMessages());
  const [isOpen, setIsOpen] = useState(false);
  const isOpenRef = useRef(isOpen);
  const [unreadCount, setUnreadCount] = useState(0);
  const [status, setStatus] = useState<CopilotStatus>("idle");
  const [processingStep, setProcessingStep] = useState(0);

  useEffect(() => {
    isOpenRef.current = isOpen;
  }, [isOpen]);

  // Remember chat state after closing: persisted to localStorage, so a
  // reopen (or a page reload) restores the same conversation.
  useEffect(() => {
    const storage = safeLocalStorage();
    storage?.setItem(MESSAGES_KEY, JSON.stringify(messages.slice(-MAX_STORED_MESSAGES)));
  }, [messages]);

  const open = useCallback(() => {
    setIsOpen(true);
    setUnreadCount(0);
  }, []);

  const close = useCallback(() => setIsOpen(false), []);

  const toggle = useCallback(() => {
    setIsOpen((wasOpen) => {
      const next = !wasOpen;
      if (next) setUnreadCount(0);
      return next;
    });
  }, []);

  const sendMessage = useCallback(async (text: string, context?: Record<string, unknown> | null) => {
    const trimmed = text.trim();
    if (!trimmed) return;

    const userMessage: ChatMessageData = {
      id: `u-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      role: "user",
      text: trimmed,
      timestamp: Date.now(),
    };
    setMessages((prev) => [...prev, userMessage]);
    setStatus("processing");
    setProcessingStep(0);

    const stepTimer = window.setInterval(() => {
      setProcessingStep((step) => Math.min(step + 1, PROCESSING_STEPS.length - 1));
    }, 550);

    try {
      const response = await fetch(`${API_BASE}/copilot/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionIdRef.current,
          message: trimmed,
          context: context ?? null,
        }),
        signal: AbortSignal.timeout(120_000),
      });
      if (!response.ok) {
        throw new Error(`GridWise Copilot returned ${response.status}`);
      }
      const data = (await response.json()) as CopilotChatResponse;

      const assistantMessage: ChatMessageData = {
        id: `a-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        role: "assistant",
        text: data.reply,
        intent: data.intent,
        actionTaken: data.action_taken,
        visualData: data.visual_data,
        timestamp: Date.now(),
      };
      setMessages((prev) => [...prev, assistantMessage]);
      setStatus("idle");
      if (!isOpenRef.current) setUnreadCount((count) => count + 1);
    } catch {
      const errorMessage: ChatMessageData = {
        id: `e-${Date.now()}`,
        role: "assistant",
        text: "I couldn't reach GridWise right now. Please check your connection and try again.",
        timestamp: Date.now(),
        isError: true,
      };
      setMessages((prev) => [...prev, errorMessage]);
      setStatus("error");
      if (!isOpenRef.current) setUnreadCount((count) => count + 1);
      window.setTimeout(() => setStatus((current) => (current === "error" ? "idle" : current)), 2500);
    } finally {
      window.clearInterval(stepTimer);
      setProcessingStep(0);
    }
  }, []);

  return {
    messages,
    isOpen,
    unreadCount,
    status,
    processingStep,
    open,
    close,
    toggle,
    sendMessage,
  };
}
