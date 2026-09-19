import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, Brain, Zap } from "lucide-react";
import { useState } from "react";
import { useAlertCount } from "@/lib/alerts";
import type { CopilotStatus } from "./types";

interface AICopilotBubbleProps {
  status: CopilotStatus;
  unreadCount: number;
  isOpen: boolean;
  onClick: () => void;
}

export default function AICopilotBubble({ status, unreadCount, isOpen, onClick }: AICopilotBubbleProps) {
  const [showTooltip, setShowTooltip] = useState(false);
  const alertCount = useAlertCount();
  const alerting = alertCount > 0 && status === "idle" && !isOpen;

  return (
    <aside aria-label="GridWise AI Copilot" className={`fixed bottom-5 right-5 z-[60] flex flex-col items-end gap-2 ${isOpen ? "max-sm:hidden" : ""}`}>
      <AnimatePresence>
        {showTooltip && !isOpen && (
          <motion.div
            initial={{ opacity: 0, x: 8 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 8 }}
            className="rounded-lg border border-white/10 bg-[#0a0f1c]/95 px-3 py-1.5 text-xs font-medium text-slate-200 shadow-xl backdrop-blur-md"
          >
            {alerting ? `${alertCount} alert${alertCount > 1 ? "s" : ""} to review` : "Ask GridWise AI"}
          </motion.div>
        )}
      </AnimatePresence>

      <motion.button
        type="button"
        aria-label="Open GridWise AI Copilot"
        onClick={onClick}
        onMouseEnter={() => setShowTooltip(true)}
        onMouseLeave={() => setShowTooltip(false)}
        whileHover={{ scale: 1.06 }}
        whileTap={{ scale: 0.94 }}
        className={`relative flex h-14 w-14 items-center justify-center rounded-full border bg-gradient-to-br from-[#0a1830] to-[#071022] ${alerting ? "border-amber-400/60 text-amber-300 shadow-[0_0_30px_-5px_rgba(251,191,36,0.55)]" : "border-cyan-400/30 text-cyan-300 shadow-[0_0_30px_-5px_rgba(34,211,238,0.5)]"}`}
      >
        {/* Animated pulse ring, idle only */}
        {status === "idle" && (
          <motion.span
            className="absolute inset-0 rounded-full border border-cyan-400/40"
            animate={{ scale: [1, 1.35, 1], opacity: [0.6, 0, 0.6] }}
            transition={{ duration: 2.2, repeat: Infinity, ease: "easeInOut" }}
          />
        )}

        <AnimatePresence mode="wait">
          {status === "processing" ? (
            <motion.span
              key="processing"
              initial={{ opacity: 0, rotate: -90 }}
              animate={{ opacity: 1, rotate: 0 }}
              exit={{ opacity: 0 }}
            >
              <motion.span
                animate={{ rotate: 360 }}
                transition={{ duration: 1.4, repeat: Infinity, ease: "linear" }}
                className="block"
              >
                <Brain size={22} />
              </motion.span>
            </motion.span>
          ) : status === "error" ? (
            <motion.span key="error" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
              <AlertTriangle size={22} className="text-amber-400" />
            </motion.span>
          ) : (
            <motion.span key="idle" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
              <Zap size={22} />
            </motion.span>
          )}
        </AnimatePresence>

        {alerting && unreadCount === 0 && (
          <span aria-hidden className="absolute -right-1 -top-1 flex h-5 min-w-[20px] items-center justify-center rounded-full bg-amber-400 px-1 text-[10px] font-bold text-slate-900 shadow-md">{alertCount}</span>
        )}

        {unreadCount > 0 && (
          <motion.span
            initial={{ scale: 0 }}
            animate={{ scale: 1 }}
            className="absolute -right-1 -top-1 flex h-5 min-w-[20px] items-center justify-center rounded-full bg-rose-500 px-1 text-[10px] font-bold text-white shadow-md"
          >
            {unreadCount > 9 ? "9+" : unreadCount}
          </motion.span>
        )}
      </motion.button>

      <span className="pr-1 text-[10px] font-medium tracking-wide text-slate-500">GridWise AI</span>
    </aside>
  );
}
