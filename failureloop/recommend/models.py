"""Stage 2 recommendation models (new schema, does not alter Stage 1)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LikelyPattern(BaseModel):
    name: str = ""
    explanation: str = ""


class NextStep(BaseModel):
    step: str
    rationale: str = ""
    supporting_incident_ids: list[str] = Field(default_factory=list)


class FixToAvoid(BaseModel):
    action: str
    why: str = ""
    incident_ids: list[str] = Field(default_factory=list)


class MatchedIncidentRef(BaseModel):
    incident_id: str
    title: str = ""
    similarity: float = 0.0
    why_matched: str = ""
    differences: str = ""


class Recommendation(BaseModel):
    """The Stage 2 recommendation payload."""

    likely_pattern: LikelyPattern = Field(default_factory=LikelyPattern)
    recommended_next_steps: list[NextStep] = Field(default_factory=list)
    fixes_to_avoid: list[FixToAvoid] = Field(default_factory=list)
    prevention_actions: list[str] = Field(default_factory=list)
    matched_incidents: list[MatchedIncidentRef] = Field(default_factory=list)
    alternative_hypotheses: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    confidence_rationale: str = ""
    data_gaps: list[str] = Field(default_factory=list)
    safety_note: str = ""

    model_config = {"extra": "ignore"}


class RecommendationOutput(BaseModel):
    """Recommendation plus which model produced it (for logging/UI)."""

    recommendation: Recommendation
    mode: str  # "stateless" | "memory"
    model_used: str = ""
