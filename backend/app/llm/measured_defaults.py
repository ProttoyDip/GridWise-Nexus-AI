"""Read credential-free benchmark selections; invalid/missing reports fail closed."""

import json
import os
from pathlib import Path

DEFAULTS_PATH = Path(__file__).with_name("default_models.json")


def load_defaults() -> dict:
    try:
        data = json.loads(DEFAULTS_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            return {}
        if not isinstance(data.get("provider_models"), dict) or not isinstance(data.get("roles"), dict):
            return {}
        return data
    except (OSError, ValueError, RecursionError):
        return {}


def ordered_models(provider: str, fallback: list[str]) -> list[str]:
    measured = load_defaults().get("provider_models", {}).get(provider, [])
    if not isinstance(measured, list) or any(not isinstance(name, str) for name in measured):
        return list(fallback)
    # Only promote known candidates; retain the complete resilience chain.
    return list(dict.fromkeys([name for name in measured if name in fallback] + fallback))


def measured_role(role: str) -> dict | None:
    selected = load_defaults().get("roles", {}).get(role)
    if not isinstance(selected, dict) or not isinstance(selected.get("provider"), str) or not isinstance(selected.get("name"), str):
        return None
    metrics = selected.get("metrics")
    if not isinstance(metrics, dict):
        return None
    for field in ("fully_correct_rate", "valid_json_rate"):
        value = metrics.get(field)
        if type(value) not in (int, float) or not 0.9 <= value <= 1:
            return None
    latency = metrics.get("median_latency_ms")
    if type(latency) not in (int, float) or not 0 <= latency < float("inf"):
        return None
    return selected


def order_interpretation_chain(chain: list) -> list:
    """Try the measured primary first across configured providers.

    Explicit model pins/lists retain the operator's complete ordering.
    Provider construction still uses its usual credential resolution.
    """
    if os.getenv("LLM_MODEL", "").strip() or any(
        os.getenv(f"{prefix}_{name.upper()}", "").strip()
        for name, _ in chain for prefix in ("LLM_MODEL", "LLM_MODELS")
    ):
        return chain
    primary = measured_role("primary")
    if not primary:
        return chain
    defaults = load_defaults().get("provider_models", {})
    def priority(entry):
        name, provider = entry
        model = getattr(provider, "model", None)
        if name == primary["provider"] and model == primary["name"]:
            return 0
        measured = defaults.get(name, [])
        return 1 if isinstance(measured, list) and model in measured else 2
    return sorted(chain, key=priority)
