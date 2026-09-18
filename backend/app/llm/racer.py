"""Parallel model racing for LLM interpretation.

Launches multiple model chains concurrently and stops as soon as enough
agreement exists, cancelling unnecessary in-flight requests.  This turns
the sequential primary → secondary → tertiary flow into a parallel race
where wall-clock time ≈ max(individual latencies) rather than sum.

Design constraints
------------------
- ``_interpret_single_note`` (with its full retry/failover/cache)
  remains the atomic unit of work per model chain — the racer is
  *above* that layer, not replacing it.
- ``_candidates_agree`` from ``app.llm.consensus`` is reused for
  agreement checking so the tolerance rules stay in one place.
- When only one chain is supplied, no racing overhead is added (direct
  call).
- Every note always yields a result; failures in individual chains
  never block or crash the race — they're logged and the remaining
  chains continue.
- Per-model and overall latency are logged at INFO level.

Cancellation semantics
----------------------
Python threads (``asyncio.to_thread``) cannot be truly interrupted
mid-HTTP-call: ``task.cancel()`` raises ``CancelledError`` in the
*asyncio wrapper* once the thread finishes its current blocking
operation, but the thread itself runs to completion.  This is the
standard ``asyncio.to_thread`` contract and is identical to the
existing ``asyncio.gather(asyncio.to_thread(...))`` pattern used
throughout ``app.llm.interpreter`` and ``app.llm.adaptive_consensus``.
The practical effect: we prevent *processing* of a late result (the
callback never fires), and if the provider's HTTP timeout expires, the
thread exits naturally.  True mid-flight cancellation would require an
async HTTP client (not the synchronous ``httpx.post`` in
``app.llm.provider``), which is outside the scope of this change.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.llm.consensus import _candidates_agree
from app.llm.interpreter import _interpret_single_note
from app.models.request import BatteryConfig, HourEntry
from app.models.response import DirectiveInterpretation

logger = logging.getLogger(__name__)

# Safety-net timeout for the entire race.  Individual provider calls
# already have their own HTTP timeout (DEFAULT_TIMEOUT_SECONDS = 30s)
# and retry budgets, so this only fires if a chain's total
# retry+failover sequence exceeds this wall-clock limit.
RACE_TIMEOUT_SECONDS: float = 45.0


@dataclass
class RaceResult:
    """Outcome of a parallel model race."""

    winner: DirectiveInterpretation | None
    """The agreed-upon interpretation, or None if no agreement was reached."""

    votes: list[tuple[str, DirectiveInterpretation]] = field(default_factory=list)
    """All (chain_label, result) pairs that completed before the race ended."""

    cancelled_count: int = 0
    """Number of chain tasks that were cancelled because agreement was
    reached before they finished."""

    race_duration_ms: float = 0.0
    """Wall-clock time for the entire race, in milliseconds."""


async def _run_chain(
    label: str,
    chain: list[tuple[str, Any]],
    note: str,
    note_index: int,
    hours: list[HourEntry],
    battery: BatteryConfig,
) -> tuple[str, DirectiveInterpretation, float]:
    """Run a single chain in a thread and return (label, result, latency_ms)."""
    start = time.perf_counter()
    result = await asyncio.to_thread(
        _interpret_single_note, chain, note, note_index, hours, battery,
    )
    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "Race: chain %r completed in %.1f ms for note %d → %s",
        label,
        elapsed_ms,
        note_index,
        result.directive_type,
    )
    return label, result, elapsed_ms


async def race_interpretations(
    chains: list[tuple[str, list[tuple[str, Any]]]],
    note: str,
    note_index: int,
    hours: list[HourEntry],
    battery: BatteryConfig,
    min_agreement: int = 2,
    timeout: float | None = None,
) -> RaceResult:
    """Race multiple model chains concurrently for the same note.

    Parameters
    ----------
    chains:
        List of ``(label, provider_chain)`` pairs.  Each ``provider_chain``
        is what ``_interpret_single_note`` expects (a list of
        ``(provider_name, provider)`` tuples).
    note:
        The operator note text.
    note_index:
        The note's position in the batch.
    hours / battery:
        Scenario context forwarded to the interpreter.
    min_agreement:
        How many chains must produce semantically equivalent results
        before the race stops early.  Default 2 (two-model agreement).
    timeout:
        Overall race timeout in seconds.  Defaults to
        ``RACE_TIMEOUT_SECONDS``.

    Returns
    -------
    RaceResult
        Contains the agreed ``winner`` (or None), all collected votes,
        the count of cancelled tasks, and the total race duration.
    """
    if timeout is None:
        timeout = RACE_TIMEOUT_SECONDS

    race_start = time.perf_counter()

    # Trivial cases: 0 or 1 chain — no racing needed.
    if not chains:
        return RaceResult(winner=None, race_duration_ms=0.0)

    if len(chains) == 1:
        label, chain = chains[0]
        try:
            lbl, result, latency = await asyncio.wait_for(
                _run_chain(label, chain, note, note_index, hours, battery),
                timeout=timeout,
            )
            elapsed = (time.perf_counter() - race_start) * 1000
            logger.info(
                "Race: finished for note %d in %.1f ms — "
                "1 votes collected, 0 cancelled, winner=%s",
                note_index,
                elapsed,
                result.directive_type,
            )
            return RaceResult(
                winner=result,
                votes=[(lbl, result)],
                race_duration_ms=elapsed,
            )
        except asyncio.TimeoutError:
            elapsed = (time.perf_counter() - race_start) * 1000
            logger.warning(
                "Race: single chain %r timed out after %.1f ms for note %d",
                label,
                elapsed,
                note_index,
            )
            return RaceResult(winner=None, race_duration_ms=elapsed)

    # Multi-chain race: launch all concurrently.
    tasks: dict[asyncio.Task, str] = {}
    for label, chain in chains:
        task = asyncio.create_task(
            _run_chain(label, chain, note, note_index, hours, battery),
            name=f"race-{label}-note{note_index}",
        )
        tasks[task] = label

    votes: list[tuple[str, DirectiveInterpretation]] = []
    winner: DirectiveInterpretation | None = None
    cancelled_count = 0

    pending = set(tasks.keys())
    try:
        async with asyncio.timeout(timeout):
            while pending:
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED,
                )
                for task in done:
                    label = tasks[task]
                    try:
                        lbl, result, latency = task.result()
                        votes.append((lbl, result))

                        # Check if we have enough agreement.
                        if winner is None and len(votes) >= min_agreement:
                            winner = _check_agreement(votes, min_agreement)
                            if winner is not None:
                                # Cancel remaining tasks.
                                for p in pending:
                                    p.cancel()
                                    cancelled_count += 1
                                logger.info(
                                    "Race: early stop for note %d — "
                                    "%d/%d chains agreed on %s, "
                                    "cancelled %d remaining",
                                    note_index,
                                    min_agreement,
                                    len(chains),
                                    winner.directive_type,
                                    cancelled_count,
                                )
                                # Drain pending so the while-loop exits.
                                pending = set()
                                break

                    except asyncio.CancelledError:
                        # Task was cancelled by us — expected.
                        pass
                    except Exception:
                        logger.warning(
                            "Race: chain %r failed for note %d",
                            label,
                            note_index,
                            exc_info=True,
                        )

    except asyncio.TimeoutError:
        # Overall race timed out.  Cancel everything still running.
        for task in pending:
            task.cancel()
            cancelled_count += 1
        logger.warning(
            "Race: overall timeout (%.1fs) for note %d after "
            "collecting %d/%d results; cancelled %d",
            timeout,
            note_index,
            len(votes),
            len(chains),
            cancelled_count,
        )

    # If no early agreement was found, check once more with all
    # collected votes (some may have arrived during the last wait).
    if winner is None and len(votes) >= min_agreement:
        winner = _check_agreement(votes, min_agreement)

    elapsed = (time.perf_counter() - race_start) * 1000
    logger.info(
        "Race: finished for note %d in %.1f ms — "
        "%d votes collected, %d cancelled, winner=%s",
        note_index,
        elapsed,
        len(votes),
        cancelled_count,
        winner.directive_type if winner else "none",
    )

    return RaceResult(
        winner=winner,
        votes=votes,
        cancelled_count=cancelled_count,
        race_duration_ms=elapsed,
    )


def _check_agreement(
    votes: list[tuple[str, DirectiveInterpretation]],
    min_agreement: int,
) -> DirectiveInterpretation | None:
    """Check if any interpretation has >= min_agreement supporting votes.

    Uses ``_candidates_agree`` from the consensus module for semantic
    equivalence (same directive_type, same hours, numeric fields within
    tolerance).

    Returns the first interpretation that reaches the threshold (in
    arrival order, so the fastest model's result is preferred), or None.
    """
    results = [r for _, r in votes]
    for i, candidate in enumerate(results):
        agreement_count = 1  # counts itself
        for j, other in enumerate(results):
            if i != j and _candidates_agree(candidate, other):
                agreement_count += 1
        if agreement_count >= min_agreement:
            return candidate
    return None
