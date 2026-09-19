import { motion } from "framer-motion";
import { CheckCircle2, Loader2, Mic, MicOff, Send, X, Zap } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import ChatMessage from "./ChatMessage";
import QuickActions from "./QuickActions";
import { useSpeechInput } from "./useSpeechInput";
import { PROCESSING_STEPS } from "./useCopilotChat";
import type { ChatMessageData, CopilotStatus } from "./types";

interface AICopilotWindowProps {
  messages: ChatMessageData[];
  status: CopilotStatus;
  processingStep: number;
  onSend: (text: string) => void;
  onClose: () => void;
}

function ProcessingTimeline({ step }: { step: number }) {
  return (
    <div className="space-y-1 rounded-lg border border-white/10 bg-black/25 px-3 py-2.5">
      {PROCESSING_STEPS.map((label, index) => {
        const done = index < step;
        const active = index === step;
        return (
          <div key={label} className="flex items-center gap-2 text-[11px]">
            {done ? (
              <CheckCircle2 size={13} className="text-emerald-400" />
            ) : active ? (
              <Loader2 size={13} className="animate-spin text-cyan-300" />
            ) : (
              <span className="block h-[13px] w-[13px] rounded-full border border-slate-600" />
            )}
            <span className={done || active ? "text-slate-200" : "text-slate-500"}>{label}</span>
          </div>
        );
      })}
    </div>
  );
}

export default function AICopilotWindow({ messages, status, processingStep, onSend, onClose }: AICopilotWindowProps) {
  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const voice = useSpeechInput(setDraft);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, status]);

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!draft.trim() || status === "processing") return;
    voice.stop();
    onSend(draft);
    setDraft("");
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 24, scale: 0.96 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 24, scale: 0.96 }}
      transition={{ duration: 0.22, ease: "easeOut" }}
      className="fixed bottom-24 right-5 z-[59] flex h-[min(600px,calc(100dvh-7rem))] w-[400px] max-w-[calc(100vw-2.5rem)] flex-col overflow-hidden rounded-2xl border border-white/10 bg-[#070c17]/90 shadow-2xl backdrop-blur-2xl sm:bottom-24 max-sm:bottom-0 max-sm:right-0 max-sm:h-[80dvh] max-sm:w-full max-sm:max-w-none max-sm:rounded-b-none max-sm:rounded-t-2xl"
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-white/10 bg-gradient-to-r from-[#0a1830] to-[#071022] px-4 py-3">
        <div className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-full bg-cyan-400/10 text-cyan-300">
            <Zap size={16} />
          </div>
          <div>
            <div className="text-sm font-semibold text-slate-100">GridWise AI Copilot</div>
            <div className="flex items-center gap-1 text-[10px] text-emerald-400">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" /> Online
            </div>
          </div>
        </div>
        <button
          type="button"
          aria-label="Close chat"
          onClick={onClose}
          className="rounded-full p-1.5 text-slate-400 transition-colors hover:bg-white/10 hover:text-slate-100"
        >
          <X size={16} />
        </button>
      </div>

      {/* Conversation area */}
      <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto px-3 py-3">
        {messages.length === 0 && (
          <div className="mt-6 space-y-2 px-2 text-center text-xs text-slate-400">
            <p className="text-slate-300">I have an AI energy engineer here for you.</p>
            <p>Ask me to optimize your energy usage, explain a decision, simulate a scenario, or check system status.</p>
          </div>
        )}
        {messages.map((message) => (
          <ChatMessage key={message.id} message={message} />
        ))}
        {status === "processing" && (
          <div className="flex justify-start">
            <div className="max-w-[85%] rounded-2xl rounded-bl-sm border border-white/10 bg-white/[0.06] px-3.5 py-2.5 backdrop-blur-md">
              <ProcessingTimeline step={processingStep} />
            </div>
          </div>
        )}
      </div>

      {/* Quick actions */}
      <div className="border-t border-white/10">
        <QuickActions onSelect={onSend} disabled={status === "processing"} />
      </div>

      {voice.error && <p role="alert" className="px-4 pt-2 text-[11px] text-amber-300">{voice.error}</p>}

      {/* Message input */}
      <form onSubmit={handleSubmit} className="flex items-center gap-2 border-t border-white/10 px-3 py-2.5">
        <input
          type="text"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={voice.listening ? "Listening..." : "Ask GridWise AI..."}
          disabled={status === "processing"}
          className="flex-1 rounded-full border border-white/10 bg-black/30 px-3.5 py-2 text-[13px] text-slate-100 placeholder:text-slate-500 outline-none focus:border-cyan-400/50 disabled:opacity-50"
        />
        {voice.supported && (
          <button
            type="button"
            onClick={voice.listening ? voice.stop : voice.start}
            disabled={status === "processing"}
            aria-label={voice.listening ? "Stop voice input" : "Start voice input"}
            aria-pressed={voice.listening}
            className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full border transition-colors disabled:opacity-40 ${voice.listening ? "animate-pulse border-rose-400/60 bg-rose-500/20 text-rose-300" : "border-white/10 bg-black/30 text-slate-300 hover:text-cyan-300"}`}
          >
            {voice.listening ? <MicOff size={15} /> : <Mic size={15} />}
          </button>
        )}
        <button
          type="submit"
          disabled={!draft.trim() || status === "processing"}
          aria-label="Send message"
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-cyan-400 to-blue-600 text-white shadow-md transition-opacity disabled:opacity-40"
        >
          <Send size={15} />
        </button>
      </form>
    </motion.div>
  );
}
