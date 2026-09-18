import { AnimatePresence } from "framer-motion";
import AICopilotBubble from "./AICopilotBubble";
import AICopilotWindow from "./AICopilotWindow";
import { useCopilotChat } from "./useCopilotChat";

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
          <AICopilotWindow
            messages={messages}
            status={status}
            processingStep={processingStep}
            onSend={sendMessage}
            onClose={close}
          />
        )}
      </AnimatePresence>
    </>
  );
}
