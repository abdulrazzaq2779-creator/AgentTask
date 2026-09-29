"""Failure fingerprint schema — the structured profile of an incident."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class AttemptedFix(BaseModel):
    action: str = Field(description="What was tried, e.g. 'Increased database capacity'")
    outcome: Literal["success", "failed", "partial", "unknown"] = Field(
        default="unknown", description="How the fix turned out"
    )
    notes: str = Field(default="", description="Why it worked or failed, if known")


class FailureFingerprint(BaseModel):
    """Structured failure pattern extracted from an incident."""

    service: str = ""
    environment: str = ""
    region: str = ""
    symptom: str = ""
    error_type: str = ""
    triggering_change: str = ""
    recent_deployment: str = ""
    infrastructure_change: str = ""
    dependency: str = ""
    affected_component: str = ""
    failure_chain: List[str] = Field(default_factory=list)
    customer_impact: str = ""
    severity: str = ""
    root_cause: str = ""
    attempted_fixes: List[AttemptedFix] = Field(default_factory=list)
    successful_fixes: List[str] = Field(default_factory=list)
    failed_fixes: List[str] = Field(default_factory=list)
    resolution_time_minutes: Optional[int] = None
    preventive_action: str = ""
    final_outcome: str = ""

    # Not extracted — assigned by the system.
    incident_id: str = ""

    model_config = {"extra": "ignore"}


class FingerprintMatch(BaseModel):
    """Result of comparing a new fingerprint against a stored one."""

    incident_id: str
    score: float
    field_scores: dict = Field(default_factory=dict)
    reasons: List[str] = Field(default_factory=list)
    stored_fingerprint: FailureFingerprint


class Recommendation(BaseModel):
    """Agent output for one incident analysis run."""

    summary: str = ""
    likely_root_cause: str = ""
    investigation_steps: List[str] = Field(default_factory=list)
    proven_fixes: List[str] = Field(default_factory=list)
    failed_fix_warnings: List[str] = Field(default_factory=list)
    prevention: str = ""
    confidence: Literal["low", "medium", "high"] = "low"
    evidence: List[str] = Field(default_factory=list)
