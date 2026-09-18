"""Extract one unambiguous JSON object, respecting strings and nesting."""

import json
import math
from typing import Any

MAX_OUTPUT_CHARACTERS = 131072


class JSONParsingError(ValueError):
    pass


class AmbiguousJSONError(JSONParsingError):
    pass


class LLMOutputError(ValueError):
    """Keep the failed output locally so a bounded retry can request repair."""

    def __init__(self, raw_output: Any, reason: str):
        super().__init__(reason)
        self.raw_output = raw_output


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise JSONParsingError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise JSONParsingError(f"Non-standard JSON constant: {value}")


def _decoder():
    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise JSONParsingError("JSON number exceeds the finite numeric range")
        return number
    return json.JSONDecoder(object_pairs_hook=_unique_keys, parse_constant=_reject_constant, parse_float=finite_float)


def _next_container(text: str, offset: int) -> int | None:
    quoted = escaped = False
    for index in range(offset, len(text)):
        char = text[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "{[":
            return index
    return None


def parse_strict_json_object(text: str | dict) -> dict:
    """Measure native/whole-response JSON validity without extraction recovery."""
    if isinstance(text, dict):
        text = json.dumps(text, allow_nan=False)
    if not isinstance(text, str) or len(text) > MAX_OUTPUT_CHARACTERS:
        raise JSONParsingError("Invalid JSON response type or size")
    result = _decoder().decode(text.lstrip("\ufeff").strip())
    if not isinstance(result, dict):
        raise JSONParsingError("JSON response must be an object")
    return result


def _one_object(values: list) -> dict:
    if len(values) > 1:
        raise AmbiguousJSONError("Multiple JSON values found; return exactly one directive object")
    if not values or not isinstance(values[0], dict):
        raise JSONParsingError("Model output must contain one JSON object")
    return values[0]


def extract_with_decoder(text: str) -> dict:
    """Use raw_decode's consumed length; never search inside decoded values."""
    decoder = _decoder()
    values = []
    offset = 0
    while (start := _next_container(text, offset)) is not None:
        value, offset = decoder.raw_decode(text, start)
        values.append(value)
    return _one_object(values)


def extract_with_brace_balancing(text: str) -> dict:
    """Fallback scanner for complete top-level containers amid non-JSON prose.

    Braces inside strings and escaped quotes do not affect depth. Incomplete
    outer objects are never replaced with one of their nested child objects.
    Balanced segments still undergo strict JSON decoding; no syntax is fixed.
    """
    stack = []
    quoted = escaped = False
    start = None
    values = []
    decoder = _decoder()
    for index, char in enumerate(text):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char in "{[":
            if not stack:
                start = index
            stack.append(char)
        elif char in "}]" and stack:
            if (stack[-1], char) not in (("{", "}"), ("[", "]")):
                raise JSONParsingError("Mismatched JSON container delimiters")
            stack.pop()
            if not stack:
                try:
                    values.append(decoder.decode(text[start:index + 1]))
                except JSONParsingError:
                    raise
                except (ValueError, RecursionError) as exc:
                    # A prose fragment such as {example} is not a JSON value.
                    # Skip its entire span, never selecting a nested object.
                    segment = text[start:index + 1].lstrip()
                    if segment.startswith("[") or segment[1:].lstrip().startswith(('"', "}")):
                        raise JSONParsingError("Malformed JSON container") from exc
    if stack or quoted:
        raise JSONParsingError("Unterminated JSON container or string")
    return _one_object(values)


def parse_json_object(text: str) -> dict:
    if not isinstance(text, str):
        raise JSONParsingError("Model output must be text or a native structured object")
    if len(text) > MAX_OUTPUT_CHARACTERS:
        raise JSONParsingError("Model output exceeds the parsing size limit")
    text = text.lstrip("\ufeff")
    try:
        return extract_with_decoder(text)
    except AmbiguousJSONError:
        raise
    except (ValueError, RecursionError):
        return extract_with_brace_balancing(text)
