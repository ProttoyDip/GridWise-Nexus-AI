"""Exact-phrase classification tests for the Copilot's deterministic
intent router, using the example phrasings from the Copilot spec."""

import pytest

from app.copilot.intent_router import Intent, classify_intent


@pytest.mark.parametrize(
    "message",
    [
        "Optimize my energy usage",
        "Reduce electricity cost",
        "Find the cheapest battery schedule",
        "optimize tomorrow's energy",
    ],
)
def test_optimization_examples(message):
    assert classify_intent(message) is Intent.OPTIMIZATION_REQUEST


@pytest.mark.parametrize(
    "message",
    [
        "Why did AI choose this schedule?",
        "Explain the battery decision",
        "Why was charging moved?",
        "why did you charge at night?",
    ],
)
def test_explanation_examples(message):
    assert classify_intent(message) is Intent.EXPLANATION_REQUEST


@pytest.mark.parametrize(
    "message",
    [
        "What if solar drops by 50%?",
        "What happens during high demand?",
        "what if solar drops?",
    ],
)
def test_simulation_examples(message):
    assert classify_intent(message) is Intent.SIMULATION_REQUEST


@pytest.mark.parametrize(
    "message",
    [
        "Is the system running?",
        "What models are active?",
        "Is optimization healthy?",
        "is system healthy?",
    ],
)
def test_status_examples(message):
    assert classify_intent(message) is Intent.STATUS_REQUEST


@pytest.mark.parametrize(
    "message",
    [
        "What is peak shaving?",
        "How does battery storage help?",
        "",
        "   ",
        "hello there",
    ],
)
def test_general_examples(message):
    assert classify_intent(message) is Intent.GENERAL_ENERGY_QUERY


def test_explanation_checked_before_optimization_for_overlapping_words():
    # Contains "cost" (optimization keyword territory) but is clearly a
    # why-question; explanation must win.
    assert classify_intent("Why does this cost so much?") is Intent.EXPLANATION_REQUEST


def test_simulation_checked_before_status_for_overlapping_words():
    assert classify_intent("What happens if the system loses solar?") is Intent.SIMULATION_REQUEST
