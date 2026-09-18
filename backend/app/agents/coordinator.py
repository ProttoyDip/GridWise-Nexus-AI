"""Optional internal enrichment after independent schedule verification."""

import os
import threading
from collections import OrderedDict

from app.explainability.generator import generate_explanation
from app.memory import store
from app.simulation.simulator import simulate

_lock = threading.Lock()
_decisions = OrderedDict()


def record_decision(request_id, scenario, response, futures=None):
    """Only called for a verified schedule. Never changes returned actions."""
    result = {
        "roles": [
            {"name": "Energy Manager", "result": "Operator notes interpreted by existing adaptive LLM routing"},
            {"name": "Safety Agent", "result": "Physical directives and schedule independently verified"},
            {"name": "Optimization Agent", "result": "Minimum grid-cost nominal schedule"},
            {"name": "Explanation Agent", "result": "Deterministic action and constraint explanations"},
        ],
        "explanation": generate_explanation(scenario, response.hourly_plan, response.directive_interpretation),
    }
    if os.getenv("GRIDWISE_DIGITAL_TWIN_ENABLED", "0") == "1":
        result["digital_twin"] = simulate(scenario, response.directive_interpretation, nominal_plan=response.hourly_plan, futures=futures)
    for directive in response.directive_interpretation:
        if directive.applies:
            # This is physical/structural success evidence, not a claim that
            # the operator's intention was independently ground-truthed.
            try:
                store.directive_memory.record(scenario.operator_notes[directive.note_index], directive.directive_type, True)
            except (ValueError, OSError):
                pass
    with _lock:
        _decisions[request_id] = result
        while len(_decisions) > 100:
            _decisions.popitem(last=False)


def get_decision(request_id):
    import copy
    with _lock:
        return copy.deepcopy(_decisions.get(request_id))
