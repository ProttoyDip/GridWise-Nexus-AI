"""Bounded feedback counts with atomic persistence and advisory matching."""

import hashlib
import json
import os
import re
import tempfile
import threading
from dataclasses import replace
from pathlib import Path

from app.llm.confidence import ConfidenceDecision

EFFECTS = {
    "solar_reduction": "Reduce available solar in the specified hours",
    "minimum_battery_reserve": "Maintain a battery energy floor",
    "no_charge_window": "Disable battery charging in the specified hours",
    "no_discharge_window": "Disable battery discharge in the specified hours",
    "max_grid_window": "Limit grid imports in the specified hours",
}


def normalize_phrase(phrase: str) -> str:
    normalized = " ".join(phrase.casefold().split())
    if not normalized or len(normalized) > 240 or re.search(r"sk-|bearer\s|api[_ -]?key|password|token\s*[:=]", normalized):
        raise ValueError("Phrase is empty, too long, or contains a credential marker")
    return normalized


class DirectiveMemory:
    def __init__(self, path: Path | None = None, max_entries: int = 256):
        self.path = path or Path(os.getenv("GRIDWISE_MEMORY_PATH", str(Path(tempfile.gettempdir()) / "gridwise-directive-memory.json")))
        self.max_entries = max_entries
        self._lock = threading.RLock()
        self._entries = {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for entry in data.get("entries", [])[-max_entries:]:
                phrase = normalize_phrase(entry["phrase"])
                kind = entry["directive"]
                successes, failures = entry["successes"], entry["failures"]
                if kind not in EFFECTS or any(type(v) is not int or v < 0 for v in (successes, failures)):
                    continue
                self._entries[(phrase, kind)] = {"phrase": phrase, "directive": kind, "successes": successes, "failures": failures}
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def record(self, phrase: str, directive: str, success: bool) -> None:
        if directive not in EFFECTS or type(success) is not bool:
            raise ValueError("Unsupported memory feedback")
        phrase = normalize_phrase(phrase)
        with self._lock:
            key = (phrase, directive)
            item = self._entries.pop(key, {"phrase": phrase, "directive": directive, "successes": 0, "failures": 0})
            item["successes" if success else "failures"] += 1
            self._entries[key] = item
            while len(self._entries) > self.max_entries:
                self._entries.pop(next(iter(self._entries)))
            # Failure to persist must not invalidate in-memory evidence.
            temp_path = None
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, delete=False) as file:
                    temp_path = Path(file.name)
                    entries = [{**e, "success_rate": e["successes"] / (e["successes"] + e["failures"])}
                               for e in self._entries.values()]
                    json.dump({"schema_version": 1, "entries": entries}, file, indent=2)
                os.replace(temp_path, self.path)
            except OSError:
                pass
            finally:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)

    def lookup(self, phrase: str) -> list[dict]:
        phrase = normalize_phrase(phrase)
        with self._lock:
            return [{**e, "success_rate": e["successes"] / (e["successes"] + e["failures"])}
                    for (p, _), e in self._entries.items() if p == phrase and e["successes"] + e["failures"] > 0]

    def context(self, phrase: str) -> str:
        evidence = self.lookup(phrase)
        # Only whitelisted type/count/rate data enters a prompt, never stored
        # phrases or free-form feedback that could inject instructions.
        return json.dumps([{k: e[k] for k in ("directive", "successes", "failures", "success_rate")} for e in evidence]) if evidence else ""

    def boost(self, phrase, directive):
        metadata = directive._confidence_metadata
        if metadata is None or metadata.decision == ConfidenceDecision.ESCALATE:
            return directive
        evidence = next((e for e in self.lookup(phrase) if e["directive"] == directive.directive_type), None)
        if evidence and evidence["successes"] >= 2 and evidence["success_rate"] >= 0.8:
            directive._confidence_metadata = replace(metadata, confidence_score=min(1.0, metadata.confidence_score + 0.05))
        # Verification decisions and model agreement are never changed.
        return directive

    def graph(self) -> dict:
        with self._lock:
            nodes = [{"id": kind, "label": kind, "type": "directive"} for kind in EFFECTS]
            edges = []
            for kind, effect in EFFECTS.items():
                nodes.append({"id": "effect:" + kind, "label": effect, "type": "effect"})
                edges.append({"source": kind, "target": "effect:" + kind})
            for (phrase, kind), entry in self._entries.items():
                phrase_id = hashlib.sha256(phrase.encode()).hexdigest()
                if not any(n["id"] == phrase_id for n in nodes):
                    nodes.append({"id": phrase_id, "label": phrase, "type": "phrase"})
                edges.append({"source": phrase_id, "target": kind, "successes": entry["successes"], "failures": entry["failures"]})
            return {"nodes": nodes, "edges": edges}


directive_memory = DirectiveMemory()
