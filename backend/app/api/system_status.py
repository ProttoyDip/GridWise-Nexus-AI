"""GET /system/status — service health, model availability, optimizer status.

Additive and judge-schema-safe, like GET /llm/status: not part of the
fixed /optimize-energy request/response contract, so it can be extended
freely. Never returns API keys or any other secret — only provider/model
ids (already public via GET /llm/status), circuit-breaker state, solver
availability, and aggregate (never per-request-detailed) monitoring
counters from app.monitoring.logger.
"""

from fastapi import APIRouter

from app.llm import circuit_breaker
from app.llm.provider import LLMProviderError, get_provider_chain
from app.monitoring import logger as monitoring_logger

router = APIRouter()


def _model_availability() -> dict:
    try:
        chain = get_provider_chain()
    except LLMProviderError as exc:
        return {"configured": False, "reason": str(exc), "models": []}

    breaker_states = {(s.provider, s.model): s for s in circuit_breaker.snapshot()}
    models = []
    for provider_name, provider in chain:
        model_id = getattr(provider, "model", "unknown")
        state = breaker_states.get((provider_name, model_id))
        models.append(
            {
                "provider": provider_name,
                "model": model_id,
                "circuit_open": bool(state and circuit_breaker.is_open(provider_name, model_id)),
                "failure_count": state.failure_count if state else 0,
            }
        )
    return {"configured": True, "models": models}


def _optimizer_status() -> dict:
    try:
        import pulp

        cbc = pulp.PULP_CBC_CMD(msg=False)
        return {"available": bool(cbc.available()), "solver": "CBC"}
    except Exception as exc:  # noqa: BLE001 - status probe must never raise
        return {"available": False, "solver": "CBC", "reason": type(exc).__name__}


@router.get("/system/status")
def get_system_status() -> dict:
    model_availability = _model_availability()
    optimizer_status = _optimizer_status()
    healthy = model_availability["configured"] and optimizer_status["available"]

    return {
        "healthy": healthy,
        "model_availability": model_availability,
        "optimizer_status": optimizer_status,
        "monitoring": monitoring_logger.summary(),
    }
