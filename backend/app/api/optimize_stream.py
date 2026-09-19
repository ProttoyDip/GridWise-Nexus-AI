"""POST /optimize-energy/stream — additive NDJSON stream of real pipeline stages.

Not part of the fixed /optimize-energy contract, which is untouched. It runs the
same handler and relays only the coarse stage events the pipeline already emits
(never model reasoning or credentials), followed by the final response.
"""

from __future__ import annotations

import json
import queue
import threading

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.api.optimize import optimize_energy
from app.models.request import ScenarioRequest
from app.monitoring.progress import observe_progress

router = APIRouter()

_slots = threading.BoundedSemaphore(4)


@router.post("/optimize-energy/stream")
def optimize_energy_stream(payload: ScenarioRequest) -> StreamingResponse:
    if not _slots.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="All optimization slots are busy. Try again shortly.")
    events: queue.Queue = queue.Queue()

    def work() -> None:
        try:
            with observe_progress(events.put):
                response = optimize_energy(payload)
            events.put({"event": "result", "data": response.model_dump(mode="json")})
        except HTTPException as exc:
            events.put({"event": "error", "status": exc.status_code, "detail": str(exc.detail)})
        except Exception:  # noqa: BLE001 - the stream must always terminate cleanly
            events.put({"event": "error", "status": 500, "detail": "Optimization failed. Try again."})
        finally:
            events.put(None)
            _slots.release()

    def stream():
        while True:
            try:
                event = events.get(timeout=10)
            except queue.Empty:
                yield json.dumps({"event": "heartbeat"}) + "\n"
                continue
            if event is None:
                return
            yield json.dumps(event, ensure_ascii=True, allow_nan=False) + "\n"

    try:
        threading.Thread(target=work, daemon=True).start()
    except Exception:
        _slots.release()
        raise
    return StreamingResponse(stream(), media_type="application/x-ndjson", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
