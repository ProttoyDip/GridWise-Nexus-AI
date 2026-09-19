import { AnimatePresence } from "framer-motion";
import { lazy, Suspense } from "react";
import AICopilotBubble from "./AICopilotBubble";
import { useCopilotChat } from "./useCopilotChat";

const AICopilotWindow = lazy(() => import("./AICopilotWindow"));

/**
 * Floating AI Energy Copilot — mount once near the root of the app
 * (see App.tsx). Purely a conversational layer over the existing
 * GridWise API (POST /copilot/chat); contains no optimization logic.
 */
export default function GridWiseCopilot() {
  const { messages, isOpen, unreadCount, status, processingStep, toggle, close, sendMessage } = useCopilotChat();

  return (
    <>
      <AICopilotBubble status={status} unreadCount={unreadCount} isOpen={isOpen} onClick={toggle} />
      <AnimatePresence>
        {isOpen && (
          <Suspense fallback={null}>
          <AICopilotWindow
            messages={messages}
            status={status}
            processingStep={processingStep}
            onSend={sendMessage}
            onClose={close}
          />
          </Suspense>
        )}
      </AnimatePresence>
    </>
  );
}
