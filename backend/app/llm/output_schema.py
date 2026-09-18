"""Provider-compatible strict shape; deterministic guardrails enforce bounds."""

from typing import get_args

from app.models.response import DirectiveType


def directive_json_schema() -> dict:
    def adjustment(numeric_field=None):
        properties = {"hours": {"type": "array", "items": {"type": "integer"}}}
        if numeric_field:
            properties[numeric_field] = {"type": "number"}
        return {"type": "object", "properties": properties,
                "required": list(properties), "additionalProperties": False}

    properties = {
        "note_index": {"type": "integer"},
        "applies": {"type": "boolean"},
        "directive_type": {"type": "string", "enum": list(get_args(DirectiveType))},
        "structured_adjustment": {"anyOf": [
            {"type": "null"}, adjustment(), adjustment("factor"),
            adjustment("minimum_energy_kwh"), adjustment("max_grid_kwh"),
        ]},
        "explanation": {"type": "string"},
    }
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}
