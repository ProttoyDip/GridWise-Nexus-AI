"""Request-local stage notifications, never model reasoning or credentials."""

from contextlib import contextmanager
from contextvars import ContextVar

_listener = ContextVar("gridwise_progress_listener", default=None)


def emit_progress(stage: int, state: str) -> None:
    listener = _listener.get()
    if listener is not None:
        try:
            listener({"event": "progress", "stage": stage, "state": state})
        except Exception:
            pass  # Observability is optional and cannot affect optimization.


@contextmanager
def observe_progress(listener):
    token = _listener.set(listener)
    try:
        yield
    finally:
        _listener.reset(token)
