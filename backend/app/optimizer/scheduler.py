"""Builds the 24-hour hourly_plan.

Takes validated directives plus the base scenario (demand, solar,
tariff, battery limits) and produces a schedule that satisfies energy
balance, battery bounds/rate limits, end-of-day neutrality, and every
applicable directive constraint, while minimizing total grid cost
(Problem Statement Sections 05, 09).
"""

# TODO: implement build_hourly_plan().
