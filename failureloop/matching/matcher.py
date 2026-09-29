"""Recall + explainable matching (Stage 2 core).

Pipeline:
1. Build TWO Hindsight recall queries:
   - pattern query: causal chain + dependency + trigger (the differentiator)
   - surface query: symptom + error type (to expose surface-only lookalikes)
   merge and deduplicate candidates.
2. Map recalled memories back to SQLite incidents by incident_id
   (metadata first, text parsing as fallback).
3. Score every candidate with named components:
   causal_chain_similarity (ONE batched LLM-judge call for all candidates),
   dependency_match, trigger_match, recency, severity_relevance.
4. Flag near-misses (surface-similar, causally different) as partial_match.
5. Cold start: empty bank or nothing above threshold -> empty result with a
   clear reason. Never fabricate matches.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from failureloop.fingerprint import FailureFingerprint
from failureloop.llm.client import LLMClient
from failureloop.llm.prompts import load_prompt
from failureloop.memory import MemoryLayer
from failureloop.storage import Storage

logger = logging.getLogger("failureloop.matching")

# Total score components (weights sum to 1.0).
COMPONENT_WEIGHTS = {
    "causal_chain_similarity": 0.50,
    "dependency_match": 0.15,
    "trigger_match": 0.15,
    "recency": 0.10,
    "severity_relevance": 0.10,
}
MIN_CONFIDENT_SCORE = 0.45
RECENCY_HALF_LIFE_DAYS = 180.0
MAX_CANDIDATES = 8

_ID_RE = re.compile(r"\bINC-\d+\b", re.IGNORECASE)


class CandidateScore(BaseModel):
    incident_id: str
    causal_similarity: float = Field(ge=0.0, le=1.0)
    rationale: str = ""


class CandidateJudgeResponse(BaseModel):
    scores: list[CandidateScore]


@dataclass
class MatchResult:
    """One scored match with its full component breakdown."""

    incident_id: str
    total: float
    components: dict[str, float]
    judge_rationale: str = ""
    match_type: str = "match"  # "match" | "partial_match"
    difference_note: str = ""
    fingerprint: FailureFingerprint | None = None

    @property
    def score(self) -> float:
        return self.total


@dataclass
class MatchingOutput:
    matches: list[MatchResult] = field(default_factory=list)
    cold_start: bool = False
    reason: str = ""


def _parse_incident_ids(text: str) -> list[str]:
    return [m.upper() for m in _ID_RE.findall(text or "")]


def build_pattern_query(fp: FailureFingerprint) -> str:
    """Recall query built from the CAUSAL CHAIN, not surface symptom."""
    chain = " -> ".join(fp.failure_chain) if fp.failure_chain else fp.root_cause
    parts = [
        "Historical incidents with this underlying failure pattern:",
        f"Causal chain: {chain or 'unknown'}",
        f"Downstream dependency: {fp.dependency or 'unknown'}",
        f"Triggering change: {fp.triggering_change or 'unknown'}",
        "Find incidents with a similar causal mechanism even when error messages differ. "
        "Include what fixes succeeded and what failed.",
    ]
    return "\n".join(parts)


def build_surface_query(fp: FailureFingerprint) -> str:
    """Second recall query using surface symptom + error type."""
    parts = [
        "Historical incidents with this surface symptom:",
        f"Service: {fp.service or 'unknown'}",
        f"Symptom: {fp.symptom or 'unknown'}",
        f"Error type: {fp.error_type or 'unknown'}",
        "Return incidents sharing these observable details.",
    ]
    return "\n".join(parts)


def _recency_score(created_at: str | None) -> float:
    """1.0 for fresh incidents, decaying with a 180-day half-life."""
    if not created_at:
        return 0.5
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (datetime.now(timezone.utc) - created).total_seconds() / 86400)
        return 0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS)
    except (ValueError, TypeError):
        return 0.5


_SEVERITY_ORDER = {"SEV-1": 4, "SEV-2": 3, "SEV-3": 2, "SEV-4": 1}


def _severity_relevance(new_fp: FailureFingerprint, stored_fp: FailureFingerprint | None) -> float:
    """1.0 when severities match; lower when they are far apart."""
    new_sev = _SEVERITY_ORDER.get((new_fp.severity or "").upper(), 0)
    stored_sev = _SEVERITY_ORDER.get((stored_fp.severity or "").upper(), 0) if stored_fp else 0
    if new_sev == 0 and stored_sev == 0:
        return 0.5
    if stored_sev == 0 or new_sev == 0:
        return 0.5
    gap = abs(new_sev - stored_sev)
    return {0: 1.0, 1: 0.7, 2: 0.4, 3: 0.2}[gap]


def _token_overlap(a: str, b: str) -> float:
    """Shared-word overlap; small helper kept local to avoid Stage 1 coupling."""

    def toks(s: str) -> set[str]:
        return {w for w in re.findall(r"[a-z0-9-]+", (s or "").lower()) if len(w) > 2}

    ta, tb = toks(a), toks(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def _judge_causal_similarity(
    llm: LLMClient,
    new_fp: FailureFingerprint,
    candidates: dict[str, FailureFingerprint],
) -> dict[str, tuple[float, str]]:
    """ONE batched LLM-judge call for all candidates."""
    if not candidates:
        return {}
    new_chain = " -> ".join(new_fp.failure_chain) if new_fp.failure_chain else "(chain unknown)"
    lines = [f"NEW incident chain: {new_chain}", "", "Candidates:"]
    for idx, (incident_id, cand_fp) in enumerate(sorted(candidates.items()), 1):
        chain = " -> ".join(cand_fp.failure_chain) if cand_fp.failure_chain else "(chain unknown)"
        lines.append(
            f"{idx}. id={incident_id} chain={chain} "
            f"dependency={cand_fp.dependency or 'unknown'} trigger={cand_fp.triggering_change or 'unknown'}"
        )
    user_prompt = (
        "Score causal similarity 0.0-1.0 between the NEW incident and EACH candidate.\n\n"
        + "\n".join(lines)
    )
    system_prompt = load_prompt("judge_causal_similarity")
    try:
        response = llm.complete_json(system_prompt, user_prompt, CandidateJudgeResponse)
        return {
            s.incident_id: (max(0.0, min(1.0, s.causal_similarity)), s.rationale)
            for s in response.scores
            if s.incident_id in candidates
        }
    except Exception as exc:
        # Judge is an enhancement, not a hard gate: degrade to token overlap.
        logger.warning("Causal judge failed (%s); falling back to token overlap.", exc)
        return {
            incident_id: (
                _token_overlap(
                    " ".join(new_fp.failure_chain), " ".join(fp.failure_chain)
                ),
                "token-overlap fallback (judge unavailable)",
            )
            for incident_id, fp in candidates.items()
        }


def find_similar_incidents(
    fingerprint: FailureFingerprint,
    top_k: int = 5,
    memory: MemoryLayer | None = None,
    storage: Storage | None = None,
    llm: LLMClient | None = None,
    min_confident_score: float = MIN_CONFIDENT_SCORE,
) -> MatchingOutput:
    """Recall + score. Returns matches plus cold-start/weak-signal reasons."""
    if memory is None or storage is None or llm is None:
        from failureloop.config import get_config

        config = get_config()
        memory = memory or MemoryLayer(config)
        storage = storage or Storage(config.db_path)
        # Local-fallback mode: SQLite doubles as the recall corpus.
        memory.seed_local_from_storage(storage)
        llm = llm or LLMClient(config)

    output = MatchingOutput()

    # ---- cold start: nothing in the bank at all
    if storage.incident_count() == 0:
        output.cold_start = True
        output.reason = (
            "Memory bank is empty: no historical incidents have been retained yet. "
            "No matches can be fabricated; analyze without history or load history first."
        )
        return output

    # ---- 1. dual recall
    pattern_hits = memory.recall_text(build_pattern_query(fingerprint), max_results=top_k * 2)
    surface_hits = memory.recall_text(build_surface_query(fingerprint), max_results=top_k * 2)

    # ---- 2. map back to SQLite incidents by incident_id
    fingerprint_map = storage.get_fingerprint_map()
    created_map = storage.get_incident_created_map()
    candidates: dict[str, dict] = {}

    def _register(hit: dict, source: str) -> None:
        metadata = hit.get("metadata") or {}
        incident_id = str(metadata.get("incident_id") or "")
        if not incident_id:
            found = _parse_incident_ids(hit.get("text") or "")
            incident_id = found[0] if found else ""
        if incident_id and incident_id in fingerprint_map and incident_id not in candidates:
            candidates[incident_id] = {
                "recall_score": float(hit.get("score") or 0.0),
                "via": {source},
            }
        elif incident_id in candidates:
            candidates[incident_id]["via"].add(source)

    for hit in pattern_hits:
        _register(hit, "pattern")
    for hit in surface_hits:
        _register(hit, "surface")

    if not candidates:
        output.cold_start = True
        output.reason = (
            "Recall found no incidents mappable to stored records. "
            "The bank may be empty or memories lack incident_id metadata."
        )
        return output

    # Cap candidates for the batched judge call.
    ranked_ids = sorted(
        candidates,
        key=lambda iid: (
            len(candidates[iid]["via"]),
            candidates[iid]["recall_score"],
        ),
        reverse=True,
    )[:MAX_CANDIDATES]
    candidate_fps = {iid: fingerprint_map[iid] for iid in ranked_ids}

    # ---- 3. score components
    judged = _judge_causal_similarity(llm, fingerprint, candidate_fps)
    now_iso = {iid: created_map.get(iid, "") for iid in candidate_fps}

    matches: list[MatchResult] = []
    for incident_id, cand_fp in candidate_fps.items():
        causal, rationale = judged.get(incident_id, (0.0, "judge returned nothing"))
        dep = _token_overlap(fingerprint.dependency, cand_fp.dependency)
        trig = _token_overlap(fingerprint.triggering_change, cand_fp.triggering_change)
        rec = _recency_score(now_iso[incident_id])
        sev = _severity_relevance(fingerprint, cand_fp)
        components = {
            "causal_chain_similarity": round(causal, 3),
            "dependency_match": round(dep, 3),
            "trigger_match": round(trig, 3),
            "recency": round(rec, 3),
            "severity_relevance": round(sev, 3),
        }
        total = sum(COMPONENT_WEIGHTS[k] * v for k, v in components.items())
        matches.append(
            MatchResult(
                incident_id=incident_id,
                total=round(total, 4),
                components=components,
                judge_rationale=rationale,
                fingerprint=cand_fp,
            )
        )

    matches.sort(key=lambda m: m.total, reverse=True)
    matches = matches[:top_k]

    # ---- 4. near-miss detection: surface-similar but causally different
    for match in matches:
        causal = match.components["causal_chain_similarity"]
        surface = match.components["dependency_match"]
        if causal < 0.4 and (surface >= 0.5 or "surface" in candidates[match.incident_id]["via"]):
            match.match_type = "partial_match"
            match.difference_note = (
                f"Shares surface details but the causal mechanism differs "
                f"(causal similarity {causal:.2f}). {match.judge_rationale}".strip()
            )
        elif causal >= 0.7 and match.total >= min_confident_score:
            match.difference_note = match.judge_rationale

    # ---- 5. weak-signal handling
    confident = [
        m for m in matches if m.match_type == "match" and m.total >= min_confident_score
    ]
    if not confident:
        output.matches = matches  # keep for transparency, flagged partial/weak
        output.cold_start = False
        output.reason = (
            f"No incident scored above the {min_confident_score:.2f} confidence threshold. "
            "Treat this as a possibly new failure pattern; matches below are weak or partial."
        )
        best = max((m.total for m in matches), default=0.0)
        if best > 0:
            output.reason += f" Best total score: {best:.2f}."
        return output

    output.matches = matches
    return output
