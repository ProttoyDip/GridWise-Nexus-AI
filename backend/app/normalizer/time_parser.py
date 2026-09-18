"""Deterministic time-expression normalizer for GridWise.

Converts natural-language time expressions (as extracted by the LLM) into
concrete lists of 24-hour integer hours. The LLM is responsible for
*extracting* the time expression from operator notes; this module is
responsible for *converting* it into the final hour list. The LLM alone
must never decide the final hours array.

Supported formats
-----------------
- Named periods: "morning" (6–12), "afternoon" (12–17), "evening" (17–21),
  "night" (21–6 wrapping), "overnight" (21–6 wrapping),
  "midnight" (hour 0), "noon" (hour 12)
- AM/PM ranges: "1 PM to 3 PM", "6 PM until 9 PM"
- 24-hour ranges: "13:00 to 15:00", "13 to 15"
- Keyword-anchored ranges: "from noon until 4 PM", "midnight to 2 AM"

All ranges are **start-inclusive, end-exclusive**:
  "1 PM to 3 PM" → [13, 14]  (hours 13 and 14, NOT 15)

Midnight-crossing ranges are supported:
  "10 PM to 2 AM" → [22, 23, 0, 1]
"""

from __future__ import annotations

import re
from typing import Optional

# ---------------------------------------------------------------------------
# Named-period definitions (start-inclusive, end-exclusive)
# ---------------------------------------------------------------------------

_NAMED_PERIODS: dict[str, list[int]] = {
    "morning":   list(range(6, 12)),
    "afternoon": list(range(12, 17)),
    "evening":   list(range(17, 21)),
    "night":     list(range(21, 24)) + list(range(0, 6)),
    "overnight": list(range(21, 24)) + list(range(0, 6)),
}

# Single named anchors (resolved to an hour, used in range endpoints)
_NAMED_ANCHORS: dict[str, int] = {
    "midnight": 0,
    "noon":     12,
}

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Matches: "1 PM", "12AM", "3:00 PM", "03:30PM" — captures hour, optional
# minute, and meridiem.  Also matches bare 24-hour like "13:00" or "13"
# (meridiem group will be empty).
_TIME_TOKEN = (
    r"(\d{1,2})"                  # hour
    r"(?::(\d{2}))?"              # optional :MM
    r"\s*(AM|PM|am|pm|a\.m\.|p\.m\.)?"  # optional meridiem
)

# Full range pattern:
#   optional "from"
#   <time-or-anchor>
#   "to" | "until" | "-" | "–" | "through"
#   <time-or-anchor>
_ANCHOR_NAMES = "|".join(_NAMED_ANCHORS.keys())
_TIME_OR_ANCHOR = rf"(?:{_TIME_TOKEN}|({_ANCHOR_NAMES}))"

_RANGE_PATTERN = re.compile(
    r"(?:from\s+)?"
    + _TIME_OR_ANCHOR
    + r"\s+(?:to|until|through|-|–)\s+"
    + _TIME_OR_ANCHOR,
    re.IGNORECASE,
)

_NAMED_PERIOD_PATTERN = re.compile(
    r"\b(" + "|".join(_NAMED_PERIODS.keys()) + r")\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_token_hour(
    hour_str: Optional[str],
    minute_str: Optional[str],
    meridiem: Optional[str],
    anchor_name: Optional[str],
) -> int:
    """Resolve a single time token to a 0–23 hour integer."""
    if anchor_name:
        return _NAMED_ANCHORS[anchor_name.lower()]

    hour = int(hour_str)  # type: ignore[arg-type]
    meridiem_upper = (meridiem or "").upper().replace(".", "")

    if meridiem_upper in ("AM", "PM"):
        # 12-hour clock conversion
        if meridiem_upper == "AM":
            if hour == 12:
                hour = 0
        else:  # PM
            if hour != 12:
                hour += 12
    else:
        # No meridiem — treat as 24-hour (0–23).
        # Values > 23 are invalid; caller validates.
        pass

    if not (0 <= hour <= 23):
        raise ValueError(f"Hour {hour} is outside the valid 0–23 range")

    return hour


def _expand_range(start: int, end: int) -> list[int]:
    """Expand an inclusive-start / exclusive-end range, wrapping at midnight.

    Returns hours in chronological order (which may not be ascending if the
    range crosses midnight).
    """
    if start == end:
        return []
    if start < end:
        return list(range(start, end))
    # Wraps past midnight: e.g. 22 → 2 becomes [22, 23, 0, 1]
    return list(range(start, 24)) + list(range(0, end))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize_time_expression(expression: str) -> list[int]:
    """Convert a natural-language time expression into a sorted, unique hour list.

    Parameters
    ----------
    expression:
        A human-readable time string such as ``"1 PM to 3 PM"``,
        ``"morning"``, or ``"from noon until 4 PM"``.

    Returns
    -------
    list[int]
        Unique hours in ascending order (0–23). Suitable for direct use
        as the ``hours`` field of a ``structured_adjustment``.

    Raises
    ------
    ValueError
        If the expression cannot be parsed into a valid hour range.
    """
    text = expression.strip()

    # 1. Try explicit range ("1 PM to 3 PM", "midnight to 2 AM", etc.)
    match = _RANGE_PATTERN.search(text)
    if match:
        groups = match.groups()
        # Groups layout (per _TIME_OR_ANCHOR × 2):
        #   0: hour_str_1,  1: minute_str_1,  2: meridiem_1,  3: anchor_1
        #   4: hour_str_2,  5: minute_str_2,  6: meridiem_2,  7: anchor_2
        start_hour = _resolve_token_hour(groups[0], groups[1], groups[2], groups[3])
        end_hour   = _resolve_token_hour(groups[4], groups[5], groups[6], groups[7])
        hours = _expand_range(start_hour, end_hour)
        if not hours:
            raise ValueError(
                f"Time range resolved to an empty hour set: start={start_hour}, end={end_hour}"
            )
        return sorted(set(hours))

    # 2. Try named period ("morning", "afternoon", etc.)
    period_match = _NAMED_PERIOD_PATTERN.search(text)
    if period_match:
        period = period_match.group(1).lower()
        return sorted(set(_NAMED_PERIODS[period]))

    raise ValueError(
        f"Could not parse time expression into hours: {expression!r}"
    )

