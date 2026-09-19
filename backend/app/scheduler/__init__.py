"""AI Action Scheduler: turns an already-optimized plan into operator actions.

Works strictly on top of optimizer output; it never changes the plan.
"""

from app.scheduler.action_generator import generate_daily_actions

__all__ = ["generate_daily_actions"]
