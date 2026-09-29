"""Stage 2 recommendation engine.

Two modes, same input, same LLM - the only difference is memory:

- recommend_stateless: the LLM sees ONLY the incident. No Hindsight, no
  history. This is the "before" in the demo.
- recommend_with_memory: matches + ranked fixes + a Hindsight reflect
  summary ground the recommendation. Citations required for memory claims.

Honesty rules enforced structurally:
- The stateless prompt forbids history and empty matched_incidents/fixes_to_avoid.
- Memory mode cites incident IDs; uncited claims must be labeled general reasoning.
- Weak evidence must lower confidence (post-processing guard clamps this).
- Every output carries a safety note: decision support, never automated action.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pydantic import BaseModel, Field, model_validator

from failureloop.fingerprint import FailureFingerprint
from failureloop.llm.client import LLMClient
from failureloop.llm.prompts import load_prompt
from failureloop.matching.fix_ranker import RankedFixes, rank_fixes
from failureloop.matching.matcher import MatchResult, find_similar_incidents
from failureloop.memory import MemoryLayer
from failureloop.recommend.models import (
    FixToAvoid,
    LikelyPattern,
    MatchedIncidentRef,
    NextStep,
    Recommendation,
    RecommendationOutput,
)
from failureloop.storage import Storage

logger = logging.getLogger("failureloop.engine")

SAFETY_NOTE = (
    "Decision support for the on-call engineer, not automated action. "
    "Verify each step before changing production; the assistant never executes changes."
)
DEFAULT_SAFETY = SAFETY_NOTE
MAX_TEXT = 12000


@dataclass
class EngineDeps:
    """Injected dependencies (makes tests and CLI wiring explicit)."""

    llm: LLMClient
    memory: MemoryLayer
    storage: Storage


class EngineLLMResponse(BaseModel):
    """Schema the LLM must return for both modes.

    Accepts the recommendation either wrapped ({"recommendation": {...}})
    or at top level (the prompt shows the bare keys); the before-validator
    normalizes to the wrapped shape.
    """

    recommendation: Recommendation

    model_config = {"extra": "ignore"}

    @model_validator(mode="before")
    @classmethod
    def _wrap_top_level(cls, data: object) -> object:
        if isinstance(data, dict) and "recommendation" not in data:
            return {"recommendation": data}
        return data


def _fingerprint_context(fp: FailureFingerprint) -> str:
    lines = [
        f"service: {fp.service or 'unknown'}",
        f"symptom: {fp.symptom or 'unknown'}",
        f"error_type: {fp.error_type or 'unknown'}",
        f"triggering_change: {fp.triggering_change or 'unknown'}",
        f"dependency: {fp.dependency or 'unknown'}",
        f"severity: {fp.severity or 'unknown'}",
        f"root_cause: {fp.root_cause or 'unknown (live incident; not yet confirmed)'}",
    ]
    if fp.failure_chain:
        lines.append("failure_chain: " + " -> ".join(fp.failure_chain))
    if fp.attempted_fixes:
        tried = "; ".join(
            f"{fix.action} [{fix.outcome}]" for fix in fp.attempted_fixes
        )
        lines.append(f"already_tried: {tried}")
    if fp.customer_impact:
        lines.append(f"customer_impact: {fp.customer_impact}")
    return "\n".join(lines)


def _matches_context(matches: list[MatchResult]) -> str:
    if not matches:
        return "MATCHED HISTORICAL INCIDENTS: none above threshold."
    lines = ["MATCHED HISTORICAL INCIDENTS (ranked):"]
    for match in matches:
        fp = match.fingerprint
        title = fp.symptom if fp else ""
        lines.append(
            f"- {match.incident_id} | total={match.total:.2f} | type={match.match_type} | "
            f"symptom={title}\n"
            f"  chain={' -> '.join(fp.failure_chain) if fp and fp.failure_chain else 'unknown'}\n"
            f"  dependency={fp.dependency if fp else 'unknown'} | "
            f"trigger={fp.triggering_change if fp else 'unknown'} | "
            f"root_cause={fp.root_cause if fp else 'unknown'}\n"
            f"  worked_fixes={fp.successful_fixes if fp else []}\n"
            f"  failed_fixes={fp.failed_fixes if fp else []}\n"
            f"  why={match.judge_rationale or match.difference_note}"
        )
    return "\n".join(lines)


def _fixes_context(ranked: RankedFixes) -> str:
    lines = ["AGGREGATED FIX EVIDENCE:"]
    if ranked.proven_fixes:
        lines.append("Worked:")
        for fix in ranked.proven_fixes:
            lines.append(
                f"- {fix.action} (worked in {fix.incident_count} incident(s): "
                f"{', '.join(fix.supporting_incident_ids)}; rank {fix.rank_score:.2f})"
            )
    if ranked.failed_fixes:
        lines.append("Failed (do NOT recommend without caveats):")
        for fix in ranked.failed_fixes:
            lines.append(
                f"- {fix.action} (failed in {', '.join(fix.supporting_incident_ids)})"
            )
    if ranked.conflicting_fixes:
        lines.append("Conflicting evidence (keep both sides visible):")
        for fix in ranked.conflicting_fixes:
            lines.append(
                f"- {fix.action}: worked in {', '.join(fix.worked_in)} but failed in "
                f"{', '.join(fix.failed_in)}"
            )
    if not ranked.has_evidence:
        lines.append("(no fix evidence found in matches)")
    return "\n".join(lines)


def _reflect_context(memory: MemoryLayer, fp: FailureFingerprint) -> str:
    chain = " -> ".join(fp.failure_chain) if fp.failure_chain else fp.symptom
    query = load_prompt("reflect_query").format(
        failure_chain=chain or "unknown",
        dependency=fp.dependency or "unknown",
        triggering_change=fp.triggering_change or "unknown",
    )
    reflection = memory.reflect_text(query)
    if not reflection:
        return (
            "MEMORY REFLECTION: (Hindsight returned no reflection; "
            "rely only on the matched incidents above.)"
        )
    return f"MEMORY REFLECTION:\n{reflection[:4000]}"


def _label_uncited(rec: Recommendation) -> Recommendation:
    """Mark memory-mode claims lacking incident IDs as general reasoning."""
    for step in rec.recommended_next_steps:
        if not step.supporting_incident_ids and "general reasoning" not in step.rationale.lower():
            step.rationale = (step.rationale + " (general reasoning)").strip()
    for fix in rec.fixes_to_avoid:
        if not fix.incident_ids and "general reasoning" not in fix.why.lower():
            fix.why = (fix.why + " (general reasoning; no historical citation)").strip()
    return rec


def _honest_confidence_guard(
    recommendation: Recommendation,
    matches: list[MatchResult],
    mode: str,
) -> Recommendation:
    """Clamp confidence so weak evidence can never look strong."""
    if mode == "stateless":
        recommendation.confidence = min(recommendation.confidence, 0.5)
        return recommendation

    strong = [
        m
        for m in matches
        if m.match_type == "match" and m.total >= 0.45
    ]
    if not strong:
        cap = 0.35
    elif len(strong) == 1:
        cap = 0.6
    else:
        cap = 0.9
    if recommendation.confidence > cap:
        logger.info(
            "confidence %.2f clamped to %.2f (strong_matches=%d)",
            recommendation.confidence,
            cap,
            len(strong),
        )
        recommendation.confidence = cap
        recommendation.confidence_rationale = (
            f"(Clamped: only {len(strong)} strong match(es).) "
            + recommendation.confidence_rationale
        ).strip()
    return recommendation


def _matched_refs(matches: list[MatchResult]) -> list[MatchedIncidentRef]:
    refs: list[MatchedIncidentRef] = []
    for match in matches:
        fp = match.fingerprint
        refs.append(
            MatchedIncidentRef(
                incident_id=match.incident_id,
                title=(fp.symptom if fp else "")[:150],
                similarity=match.total,
                why_matched=match.judge_rationale or "pattern overlap",
                differences=(
                    match.difference_note
                    if match.match_type == "partial_match"
                    else ""
                ),
            )
        )
    return refs


def recommend_stateless(
    fingerprint: FailureFingerprint,
    raw_text: str,
    llm: LLMClient,
    incident_id: str = "",
    save: bool = True,
    storage: Storage | None = None,
) -> RecommendationOutput:
    """Generate a recommendation with NO memory: the "before" of the demo."""
    system_prompt = load_prompt("stateless_recommendation")
    user_prompt = (
        "Current incident. You have no access to any incident history.\n\n"
        f"INCIDENT_ID: {incident_id or 'unassigned'}\n\n"
        f"RAW INCIDENT TEXT:\n{raw_text.strip()[:MAX_TEXT]}\n\n"
        f"EXTRACTED FINGERPRINT:\n{_fingerprint_context(fingerprint)}"
    )
    response = llm.complete_json(system_prompt, user_prompt, EngineLLMResponse)
    rec = _honest_confidence_guard(response.recommendation, [], "stateless")
    rec.matched_incidents = []
    if not rec.safety_note:
        rec.safety_note = DEFAULT_SAFETY
    if save:
        storage = storage or _default_storage()
        storage.save_recommendation_payload(
            incident_id=incident_id,
            mode="stateless",
            confidence=rec.confidence,
            raw_text=raw_text,
            payload_json=rec.model_dump_json(),
        )
    return RecommendationOutput(recommendation=rec, mode="stateless", model_used=llm.config.llm_model)


def recommend_with_memory(
    fingerprint: FailureFingerprint,
    raw_text: str,
    deps: EngineDeps,
    incident_id: str = "",
    top_k: int = 5,
    save: bool = True,
    matching: object | None = None,
    ranked: RankedFixes | None = None,
) -> RecommendationOutput:
    """Memory-grounded recommendation: recall, rank, reflect, generate.

    `matching`/`ranked` may be precomputed by callers (e.g. the UI) that
    already display them, avoiding a duplicate recall + judge pass.
    """
    if matching is None:
        matching = find_similar_incidents(
            fingerprint,
            top_k=top_k,
            memory=deps.memory,
            storage=deps.storage,
            llm=deps.llm,
        )
    if ranked is None:
        ranked = rank_fixes(matching.matches)  # type: ignore[union-attr]
    system_prompt = load_prompt("memory_recommendation")
    user_prompt = (
        "Current incident:\n\n"
        f"INCIDENT_ID: {incident_id or 'unassigned'}\n\n"
        f"RAW INCIDENT TEXT:\n{raw_text.strip()[:MAX_TEXT]}\n\n"
        f"EXTRACTED FINGERPRINT:\n{_fingerprint_context(fingerprint)}\n\n"
        f"{_matches_context(matching.matches)}\n\n"
        f"{_fixes_context(ranked)}\n\n"
        f"{_reflect_context(deps.memory, fingerprint)}\n\n"
        "Generate the memory-grounded recommendation JSON now. Cite incident IDs "
        "for every memory-based claim."
    )
    response = deps.llm.complete_json(system_prompt, user_prompt, EngineLLMResponse)
    rec = _honest_confidence_guard(response.recommendation, matching.matches, "memory")
    rec = _label_uncited(rec)
    rec.matched_incidents = _matched_refs(matching.matches)
    if not rec.safety_note:
        rec.safety_note = DEFAULT_SAFETY
    if matching.reason:
        rec.data_gaps = list(rec.data_gaps) + [f"Memory note: {matching.reason}"]
    if save:
        deps.storage.save_recommendation_payload(
            incident_id=incident_id,
            mode="memory",
            confidence=rec.confidence,
            raw_text=raw_text,
            payload_json=rec.model_dump_json(),
        )
    return RecommendationOutput(
        recommendation=rec, mode="memory", model_used=deps.llm.config.llm_model
    )


def _default_storage() -> Storage:
    from failureloop.config import get_config

    return Storage(get_config().db_path)
