"""Interpret, validate, optimize, and independently verify before responding."""

import logging
import os
import time
import uuid

from fastapi import APIRouter, HTTPException

from app.guardrails.validator import validate_directive_interpretation
# ``interpret_operator_notes`` is aliased to the adaptive consensus
# entrypoint so that pre-existing tests which monkeypatch this name on
# the ``optimize`` module (e.g. tests/test_schedule_checker.py) keep
# working without modification. The underlying behavior — risk-based
# routing among LOW/MEDIUM/HIGH verification tiers — is fully backward
# compatible: the response schema is unchanged, confidence stays
# internal, and the existing provider-chain/repair/arbiter fallbacks
# are reused (see app.llm.adaptive_consensus).
from app.llm.adaptive_consensus import (
    interpret_operator_notes_with_adaptive_consensus as interpret_operator_notes,
)
from app.models.request import ScenarioRequest
from app.models.response import DirectiveInterpretation, HourlyPlanEntry, OptimizeResponse
from app.monitoring import logger as monitoring_logger
from app.monitoring.progress import emit_progress
from app.optimizer.cache import optimization_cache, optimization_cache_key
from app.optimizer.scheduler import build_hourly_plan
from app.optimizer.solver import OptimizationError
from app.verifier.schedule_checker import recalculate_totals, verify_schedule

router = APIRouter()
logger = logging.getLogger(__name__)


def _enrich(request_id, payload, response, futures):
    try:
        from app.agents.coordinator import record_decision
        record_decision(request_id, payload, response, futures)
    except Exception as exc:
        logger.warning("Optional decision enrichment failed (%s)", type(exc).__name__)
    emit_progress(5, "completed")
    return response


def _record_monitoring(
    request_id: str,
    start_time: float,
    directives: list[DirectiveInterpretation],
    optimizer_time_seconds: float,
) -> None:
    """Best-effort observability record for this request. Derives every
    field from data already computed for the response (confidence
    metadata's public "provider/model" ids, decision labels, and
    explanation-text markers already used elsewhere in the pipeline) —
    never the operator note text, raw LLM output, or any credential.
    Never allowed to fail the actual request; see app.monitoring.logger.
    """
    try:
        models_used: set[str] = set()
        consensus_result: list[str] = []
        fallback_count = 0
        validation_failures = 0
        for directive in directives:
            metadata = getattr(directive, "_confidence_metadata", None)
            if metadata is not None:
                models_used.update(metadata.models_used)
                consensus_result.append(metadata.decision.value)
            else:
                consensus_result.append("unknown")
            explanation = directive.explanation or ""
            if "guardrail validation failed" in explanation:
                validation_failures += 1
            elif "Falling back to no_op" in explanation:
                fallback_count += 1
        monitoring_logger.record_request(
            request_id=request_id,
            latency_seconds=time.perf_counter() - start_time,
            models_used=tuple(sorted(models_used)),
            fallback_count=fallback_count,
            consensus_result=tuple(consensus_result),
            optimizer_time_seconds=optimizer_time_seconds,
            validation_failures=validation_failures,
        )
    except Exception as exc:  # noqa: BLE001 - monitoring must never break a request
        logger.warning("Monitoring record failed (%s)", type(exc).__name__)


def _verified_response(
    payload: ScenarioRequest, directives: list[DirectiveInterpretation],
    plan: list[HourlyPlanEntry], cached_cost: float | None = None,
) -> OptimizeResponse:
    emit_progress(5, "running")
    totals = recalculate_totals(payload.hours, plan)
    response = OptimizeResponse(
        scenario_id=payload.scenario_id, directive_interpretation=directives,
        hourly_plan=plan, total_grid_kwh=totals.total_grid_kwh,
        total_cost_bdt=totals.total_cost_bdt if cached_cost is None else cached_cost,
        peak_grid_kwh=totals.peak_grid_kwh,
        plan_summary=(
            "Minimum operating-cost schedule including configured battery wear, while satisfying validated directives and battery limits."
            if payload.battery.degradation_cost_bdt_per_kwh > 0
            else "Minimum grid-cost schedule satisfying validated directives and battery limits."
        ),
    )
    verify_schedule(payload, response)
    return response


@router.post("/optimize-energy", response_model=OptimizeResponse)
def optimize_energy(payload: ScenarioRequest) -> OptimizeResponse:
    request_id = str(uuid.uuid4())
    start_time = time.perf_counter()
    directives: list[DirectiveInterpretation] = []
    optimizer_time_seconds = 0.0
    try:
        emit_progress(1, "running")
        interpreted = interpret_operator_notes(payload.operator_notes, payload.hours, payload.battery)
        emit_progress(1, "completed")
        emit_progress(2, "completed")
        emit_progress(3, "running")
        directives = validate_directive_interpretation(
            interpreted,
            battery=payload.battery,
        )
        emit_progress(3, "completed")
        futures = None
        if os.getenv("GRIDWISE_DIGITAL_TWIN_ENABLED", "0") == "1":
            try:
                from app.simulation.scenario_generator import generate_scenarios
                futures = generate_scenarios(payload)
            except Exception as exc:
                logger.warning("Optional future generation failed (%s)", type(exc).__name__)
        key = None
        try:
            key = optimization_cache_key(payload.scenario_id, payload.hours, payload.battery, directives, payload.flexible_loads)
            cached = optimization_cache.get(key)
            if cached is not None:
                try:
                    emit_progress(4, "completed")
                    response = _verified_response(payload, directives, cached.hourly_plan, cached.total_cost_bdt)
                    return _enrich(request_id, payload, response, futures)
                except Exception as exc:
                    # Corrupt/stale entries are treated as misses, never returned.
                    logger.warning("Discarding invalid optimization cache result (%s)", type(exc).__name__)
                    optimization_cache.discard(key)
        except Exception as exc:
            # Cache infrastructure is optional, including key generation.
            logger.warning("Optimization cache lookup failed (%s)", type(exc).__name__)
        optimizer_start = time.perf_counter()
        emit_progress(4, "running")
        plan = build_hourly_plan(payload, directives)
        optimizer_time_seconds = time.perf_counter() - optimizer_start
        emit_progress(4, "completed")
        response = _verified_response(payload, directives, plan)
        if key is not None:
            try:
                optimization_cache.put(key, plan, response.total_cost_bdt)
            except Exception as exc:
                logger.warning("Optimization cache storage failed (%s)", type(exc).__name__)
        return _enrich(request_id, payload, response, futures)
    except OptimizationError as exc:
        raise HTTPException(status_code=422, detail=f"Optimization failed: {exc}") from exc
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        raise HTTPException(status_code=500, detail=f"Schedule verification failed: {exc}") from exc
    finally:
        _record_monitoring(request_id, start_time, directives, optimizer_time_seconds)
