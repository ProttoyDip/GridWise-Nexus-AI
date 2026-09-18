"""Risk-routed interpretation with parallel model racing.

Per-note flow:

1. ``classify_risk_from_note`` (text-only, no LLM call) gives an initial
   tier — LOW/MEDIUM/HIGH.
2. ``_interpret_single_note`` produces the primary interpretation.
3. The primary interpretation refines the risk tier via
   ``refine_risk_with_directive`` — a note that looks LOW in text but
   yields a numeric directive is at least MEDIUM.
4. Verification effort scales with the refined tier:

   - LOW: keep the primary result as-is (the existing
     ``_interpret_single_note`` already does the full provider-chain
     failover/retry/repair, so LOW notes are still robust — they just
     skip the *additional* consensus/arbiter step).
   - MEDIUM: **race** primary + secondary concurrently. If they agree
     → return primary. If they disagree → call the curated arbiter.
   - HIGH: **race** primary + secondary + tertiary concurrently.
     Early-stop on first 2-of-3 agreement, cancelling remaining
     models. Disagreement → arbiter.

5. Single-provider safety: if at most one provider is configured, the
   HIGH and MEDIUM paths automatically collapse to a single-call LOW
   result. The existing public-sample test
   (``test_public_sample_pipeline``) requires ``provider.calls ==
   len(operator_notes)`` under a single-provider fixture, so this is a
   hard contract — adaptive consensus must never make extra calls when
   there is nothing to compare against.

Model racing (see ``app.llm.racer``) launches all chains via
``asyncio.create_task(asyncio.to_thread(_interpret_single_note, ...))``
so they execute truly concurrently.  Wall-clock time ≈ max(individual
latencies) rather than sum.  Per-model latency is logged at INFO level.

Like ``app.llm.consensus``, this module never raises for LLM-side
failures: every note yields exactly one ``DirectiveInterpretation``.
It does not modify ``OptimizeResponse`` or ``DirectiveInterpretation``
in any way — consensus/arbitration outcomes are surfaced via the
existing free-text ``explanation`` field only. Confidence stays
internal (``_confidence_metadata`` PrivateAttr on each result).
"""

from __future__ import annotations

import asyncio
import logging

from app.llm.consensus import (
    _arbitrate,
    _build_chain_for_spec,
    _candidates_agree,
    _fallback_chain,
    _kept_primary_after_disagreement,
    _select_secondary_spec,
    _NUMERIC_FIELD_BY_DIRECTIVE,
)
from app.llm.interpreter import (
    _fallback_no_op,
    _interpret_single_note,
)
from app.llm.model_registry import (
    PRIMARY_INTERPRETERS,
    ModelSpec,
    _configured_provider_names,
    get_arbiter_model,
    get_primary_model,
)
from app.llm.racer import race_interpretations
from app.llm.risk_classifier import (
    RiskLevel,
    classify_note_risk,
    refine_risk_with_directive,
)
from app.llm.confidence import ConfidenceDecision, assess_confidence
from app.models.request import BatteryConfig, HourEntry
from app.models.response import DirectiveInterpretation

logger = logging.getLogger(__name__)


def _resolve_refined_risk(note: str, primary: DirectiveInterpretation) -> RiskLevel:
    """Combine the text-only classification with the primary's
    structured_adjustment to get the tier the rest of the pipeline
    trusts."""
    base = classify_note_risk(note)
    return refine_risk_with_directive(base, primary)


def _select_third_spec(
    exclude_provider_a: str, exclude_provider_b: str
) -> ModelSpec | None:
    """Pick a third model on a provider distinct from both the primary
    and the secondary, preferring another curated primary interpreter
    in priority order. None if no such provider is configured."""
    configured = _configured_provider_names()
    for spec in sorted(PRIMARY_INTERPRETERS, key=lambda s: s.priority):
        if (
            spec.provider != exclude_provider_a
            and spec.provider != exclude_provider_b
            and spec.provider in configured
        ):
            return spec
    return None


def _high_path_majority(
    primary: DirectiveInterpretation,
    secondary: DirectiveInterpretation,
    tertiary: DirectiveInterpretation,
) -> DirectiveInterpretation | None:
    """Pick a winner among three independent interpretations using the
    same semantic-equivalence key as ``assess_confidence``. Returns
    the majority winner, or None if there is no majority (i.e. 3-way
    disagreement)."""
    votes = [
        ("primary", primary),
        ("secondary", secondary),
        ("tertiary", tertiary),
    ]
    winner, metadata = assess_confidence(
        [(name, _project_for_confidence(v)) for name, v in votes], []
    )
    if metadata.decision == ConfidenceDecision.ACCEPT and winner is not None:
        # Pick the actual interpretation object whose semantic_key
        # matched the majority so we keep the original explanation
        # and structured_adjustment verbatim rather than rebuilding
        # from the projected vote.
        for _, candidate in votes:
            if (
                candidate.directive_type == winner.directive_type
                and candidate.applies == winner.applies
                and (candidate.structured_adjustment or {}).get("hours")
                == (winner.structured_adjustment or {}).get("hours")
            ):
                if candidate is primary:
                    return primary
                if candidate is secondary:
                    return secondary
                if candidate is tertiary:
                    return tertiary
        return winner
    return None


def _project_for_confidence(directive: DirectiveInterpretation) -> DirectiveInterpretation:
    """Pass-through — ``assess_confidence`` already reads every field it
    needs from a real ``DirectiveInterpretation``. Kept as a hook so
    tests can monkeypatch it if needed."""
    return directive


def _interpret_low_path(
    note: str,
    note_index: int,
    primary_chain: list[tuple[str, object]],
    hours: list[HourEntry],
    battery: BatteryConfig,
) -> DirectiveInterpretation:
    return _interpret_single_note(primary_chain, note, note_index, hours, battery)  # type: ignore[arg-type]


def _interpret_medium_path(
    note: str,
    note_index: int,
    primary_chain: list[tuple[str, object]],
    hours: list[HourEntry],
    battery: BatteryConfig,
) -> DirectiveInterpretation:
    primary = _interpret_single_note(primary_chain, note, note_index, hours, battery)  # type: ignore[arg-type]
    primary_provider = primary_chain[0][0]
    secondary_spec = _select_secondary_spec(exclude_provider=primary_provider)
    if secondary_spec is None:
        logger.info(
            "Adaptive MEDIUM note %d: no alternate provider configured, accepting primary",
            note_index,
        )
        return primary
    secondary_chain = _build_chain_for_spec(secondary_spec)
    if not secondary_chain:
        return primary
    secondary = _interpret_single_note(secondary_chain, note, note_index, hours, battery)  # type: ignore[arg-type]
    if _candidates_agree(primary, secondary):
        return primary
    return _arbitrate(note, note_index, hours, battery, primary, secondary)  # type: ignore[arg-type]


def _interpret_high_path(
    note: str,
    note_index: int,
    primary_chain: list[tuple[str, object]],
    hours: list[HourEntry],
    battery: BatteryConfig,
) -> DirectiveInterpretation:
    primary = _interpret_single_note(primary_chain, note, note_index, hours, battery)  # type: ignore[arg-type]
    primary_provider = primary_chain[0][0]
    secondary_spec = _select_secondary_spec(exclude_provider=primary_provider)
    if secondary_spec is None:
        logger.info(
            "Adaptive HIGH note %d: no alternate provider, degrading to primary-only",
            note_index,
        )
        return primary
    secondary_chain = _build_chain_for_spec(secondary_spec)
    secondary = _interpret_single_note(secondary_chain, note, note_index, hours, battery)  # type: ignore[arg-type]
    if _candidates_agree(primary, secondary):
        return primary
    third_spec = _select_third_spec(primary_provider, secondary_spec.provider)
    if third_spec is not None:
        third_chain = _build_chain_for_spec(third_spec)
        if third_chain:
            tertiary = _interpret_single_note(third_chain, note, note_index, hours, battery)  # type: ignore[arg-type]
            winner = _high_path_majority(primary, secondary, tertiary)
            if winner is not None:
                return winner
            return _arbitrate(note, note_index, hours, battery, primary, secondary)  # type: ignore[arg-type]
    if get_arbiter_model() is not None:
        return _arbitrate(note, note_index, hours, battery, primary, secondary)  # type: ignore[arg-type]
    return _kept_primary_after_disagreement(
        primary, secondary, "no third model and no arbiter configured"
    )


async def _interpret_note_racing(
    note: str,
    note_index: int,
    hours: list[HourEntry],
    battery: BatteryConfig,
) -> DirectiveInterpretation:
    """Async implementation of adaptive consensus with parallel model racing.

    The primary interpretation is always computed first (needed for risk
    refinement).  For MEDIUM and HIGH tiers, verification models are then
    raced concurrently via ``race_interpretations`` instead of being
    called sequentially.
    """
    primary_spec = get_primary_model()
    if primary_spec is not None:
        primary_chain = _build_chain_for_spec(primary_spec)
    else:
        primary_chain = _fallback_chain()
    if not primary_chain:
        return _fallback_no_op(note_index, "no LLM provider configured")

    # The primary must run first: risk refinement needs the primary's
    # structured_adjustment to decide whether to race additional models.
    primary = await asyncio.to_thread(
        _interpret_single_note, primary_chain, note, note_index, hours, battery,
    )
    refined_risk = _resolve_refined_risk(note, primary)

    if refined_risk == RiskLevel.LOW:
        return primary

    primary_provider = primary_chain[0][0]

    if refined_risk == RiskLevel.MEDIUM:
        secondary_spec = _select_secondary_spec(exclude_provider=primary_provider)
        if secondary_spec is None:
            logger.info(
                "Adaptive MEDIUM note %d: no alternate provider, accepting primary",
                note_index,
            )
            return primary
        secondary_chain = _build_chain_for_spec(secondary_spec)
        if not secondary_chain:
            return primary

        # Race: primary is already done, just race the secondary.
        # We use the racer with a single chain here for consistency
        # and latency logging, but the real parallelism benefit comes
        # from the HIGH path.
        race_result = await race_interpretations(
            chains=[("secondary", secondary_chain)],
            note=note,
            note_index=note_index,
            hours=hours,
            battery=battery,
            min_agreement=1,
        )

        if race_result.winner is not None:
            secondary = race_result.winner
            if _candidates_agree(primary, secondary):
                return primary
            return _arbitrate(note, note_index, hours, battery, primary, secondary)  # type: ignore[arg-type]

        # Secondary failed entirely — accept primary.
        return primary

    # HIGH path: race secondary + tertiary concurrently.
    secondary_spec = _select_secondary_spec(exclude_provider=primary_provider)
    if secondary_spec is None:
        logger.info(
            "Adaptive HIGH note %d: no alternate provider, accepting primary",
            note_index,
        )
        return primary
    secondary_chain = _build_chain_for_spec(secondary_spec)
    if not secondary_chain:
        return primary

    # Build the list of chains to race.
    race_chains: list[tuple[str, list]] = [("secondary", secondary_chain)]

    third_spec = _select_third_spec(primary_provider, secondary_spec.provider)
    if third_spec is not None:
        third_chain = _build_chain_for_spec(third_spec)
        if third_chain:
            race_chains.append(("tertiary", third_chain))

    # Race all verification models concurrently.
    # min_agreement=1 here because we manually check agreement with
    # the primary afterward — the racer returns all results and we
    # use the existing agreement/majority logic.
    race_result = await race_interpretations(
        chains=race_chains,
        note=note,
        note_index=note_index,
        hours=hours,
        battery=battery,
        # If 2 verification models agree with each other, that's
        # already a signal — but we need to compare against primary too.
        min_agreement=len(race_chains),
    )

    # Collect results by label.
    results_by_label: dict[str, DirectiveInterpretation] = {
        label: result for label, result in race_result.votes
    }
    secondary = results_by_label.get("secondary")
    tertiary = results_by_label.get("tertiary")

    # Check agreement with primary.
    if secondary is not None and _candidates_agree(primary, secondary):
        return primary

    if tertiary is not None and _candidates_agree(primary, tertiary):
        return primary

    # No direct agreement with primary — check 3-way majority if we
    # have all three results.
    if secondary is not None and tertiary is not None:
        winner = _high_path_majority(primary, secondary, tertiary)
        if winner is not None:
            return winner
        # 3-way disagreement → arbiter.
        return _arbitrate(note, note_index, hours, battery, primary, secondary)  # type: ignore[arg-type]

    # Only secondary completed (tertiary failed or wasn't available).
    if secondary is not None:
        if get_arbiter_model() is not None:
            return _arbitrate(note, note_index, hours, battery, primary, secondary)  # type: ignore[arg-type]
        return _kept_primary_after_disagreement(
            primary, secondary, "no third model and no arbiter configured"
        )

    # Nothing completed — accept primary.
    return primary


def interpret_note_with_adaptive_consensus(
    note: str,
    note_index: int,
    hours: list[HourEntry],
    battery: BatteryConfig,
) -> DirectiveInterpretation:
    """Interpret a single operator note, scaling verification effort to
    the risk tier derived from the note's text and the primary
    interpretation's structure.  Uses parallel model racing for MEDIUM
    and HIGH tiers.  Never raises; always returns exactly one
    ``DirectiveInterpretation`` for ``note_index``."""
    return asyncio.run(
        _interpret_note_racing(note, note_index, hours, battery)
    )


async def interpret_operator_notes_with_adaptive_consensus_async(
    operator_notes: list[str],
    hours: list[HourEntry],
    battery: BatteryConfig,
) -> list[DirectiveInterpretation]:
    """Concurrent adaptive interpretation with parallel model racing.

    Each note's verification is independent, so we run them in parallel.
    Within each note, MEDIUM/HIGH tier verification models are also
    raced concurrently.  Order is preserved by ``asyncio.gather``."""
    tasks = [
        _interpret_note_racing(note, index, hours, battery)
        for index, note in enumerate(operator_notes)
    ]
    return list(await asyncio.gather(*tasks))


def interpret_operator_notes_with_adaptive_consensus(
    operator_notes: list[str],
    hours: list[HourEntry],
    battery: BatteryConfig,
) -> list[DirectiveInterpretation]:
    """Synchronous entrypoint mirroring ``interpret_operator_notes`` —
    concurrent under the hood, plain blocking call at the surface.
    Do not call from inside a running asyncio event loop; use the
    ``_async`` variant there instead."""
    return asyncio.run(
        interpret_operator_notes_with_adaptive_consensus_async(
            operator_notes, hours, battery
        )
    )
