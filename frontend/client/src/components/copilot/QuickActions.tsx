import { motion } from "framer-motion";
import { Activity, MessageCircleQuestion, Sparkles, Zap } from "lucide-react";

interface QuickAction {
  label: string;
  message: string;
  icon: React.ReactNode;
}

const ACTIONS: QuickAction[] = [
  { label: "Optimize Energy", message: "Optimize the current energy scenario", icon: <Zap size={14} /> },
  { label: "Explain Schedule", message: "Why did you choose this schedule?", icon: <MessageCircleQuestion size={14} /> },
  { label: "Simulate Scenario", message: "What if solar drops by 50%?", icon: <Sparkles size={14} /> },
  { label: "System Status", message: "Is the system healthy?", icon: <Activity size={14} /> },
];

interface QuickActionsProps {
  onSelect: (message: string) => void;
  disabled?: boolean;
}

export default function QuickActions({ onSelect, disabled }: QuickActionsProps) {
  return (
    <div className="grid grid-cols-2 gap-2 px-3 py-2">
      {ACTIONS.map((action) => (
        <motion.button
          key={action.label}
          type="button"
          disabled={disabled}
          onClick={() => onSelect(action.message)}
          whileTap={{ scale: 0.96 }}
          whileHover={{ y: -1 }}
          className="flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/5 px-2.5 py-2 !text-[11px] font-medium text-slate-200 backdrop-blur-md transition-colors hover:border-cyan-400/40 hover:bg-cyan-400/10 disabled:cursor-not-allowed disabled:opacity-40"
        >
          <span className="text-cyan-300">{action.icon}</span>
          <span className="min-w-0 leading-tight">{action.label}</span>
        </motion.button>
      ))}
    </div>
  );
}
