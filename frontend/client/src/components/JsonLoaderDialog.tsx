import { AnimatePresence, motion } from "framer-motion";
import { Braces, Upload, X } from "lucide-react";
import { useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";

type JsonLoaderDialogProps = {
  open: boolean;
  value: string;
  onChange: (value: string) => void;
  error?: string | null;
  onApply: () => void;
  onClose: () => void;
};

/** Modal for pasting a scenario object. Rendered in a portal so no card transform or blur can misplace it. */
export function JsonLoaderDialog({ open, value, error, onChange, onApply, onClose }: JsonLoaderDialogProps) {
  const titleId = useId();
  const textarea = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    textarea.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = previous;
      window.removeEventListener("keydown", onKey);
    };
  }, [open, onClose]);

  return createPortal(
    <AnimatePresence>
      {open && (
        <motion.div
          className="modal-backdrop"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.16 }}
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) onClose();
          }}
        >
          <motion.div
            className="json-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            initial={{ opacity: 0, y: 16, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 16, scale: 0.98 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
          >
            <div className="json-dialog-head">
              <div>
                <h2 id={titleId}><Braces size={16} /> Paste scenario JSON</h2>
                <p>Paste a complete scenario object: 24 hourly rows, 1–3 operator notes, and a battery.</p>
              </div>
              <button type="button" className="icon-button" onClick={onClose} aria-label="Close"><X size={16} /></button>
            </div>
            <textarea ref={textarea} value={value} onChange={(event) => onChange(event.target.value)} spellCheck={false} aria-label="Scenario JSON" aria-invalid={Boolean(error)} />
            {error && <p role="alert" className="json-dialog-error">{error}</p>}
            <div className="json-dialog-actions">
              <button type="button" className="button button-quiet" onClick={onClose}>Cancel</button>
              <button type="button" className="button button-primary" onClick={onApply}><Upload size={15} /> Apply to builder</button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}
