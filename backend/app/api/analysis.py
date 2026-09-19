"""Preview interpreted instructions and diagnose infeasible combinations."""

from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException

from app.diagnostics import diagnose_conflicts
from app.guardrails.validator import validate_directive_interpretation
from app.llm.adaptive_consensus import interpret_operator_notes_with_adaptive_consensus
from app.models.request import ScenarioRequest
from app.models.response import DirectiveInterpretation

router = APIRouter()


class ConflictAnalysis(BaseModel):
    feasible: bool
    conflicting_note_indices: list[int] = Field(default_factory=list)
    conflicting_directive_types: list[str] = Field(default_factory=list)
    hours: list[int] = Field(default_factory=list)
    summary: str
    suggestions: list[str] = Field(default_factory=list)


class ScenarioAnalysisResponse(BaseModel):
    directives: list[DirectiveInterpretation]
    conflict: ConflictAnalysis


@router.post("/analyze-scenario", response_model=ScenarioAnalysisResponse)
def analyze_scenario(payload: ScenarioRequest) -> ScenarioAnalysisResponse:
    """Interpret without committing a run, then verify joint feasibility."""
    try:
        interpreted = interpret_operator_notes_with_adaptive_consensus(
            payload.operator_notes,
            payload.hours,
            payload.battery,
        )
        directives = validate_directive_interpretation(
            interpreted,
            battery=payload.battery,
        )
        diagnosis = diagnose_conflicts(payload, directives)
        return ScenarioAnalysisResponse(
            directives=directives,
            conflict=ConflictAnalysis(
                feasible=diagnosis.feasible,
                conflicting_note_indices=list(diagnosis.conflicting_note_indices),
                conflicting_directive_types=list(diagnosis.conflicting_directive_types),
                hours=list(diagnosis.hours),
                summary=diagnosis.summary,
                suggestions=list(diagnosis.suggestions),
            ),
        )
    except (ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(status_code=422, detail=f"Scenario analysis failed: {exc}") from exc
