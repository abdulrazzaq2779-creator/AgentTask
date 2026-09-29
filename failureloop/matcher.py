"""Fingerprint pattern matching + outcome-aware ranking.

The matcher compares fingerprints field by field. The failure chain carries
the highest weight because it encodes the underlying failure mechanism; exact
error text carries the lowest weight because different messages can describe
the same failure.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from failureloop.fingerprint import FailureFingerprint, FingerprintMatch

# Field weights (sum to 1.0).
WEIGHTS = {
    "service": 0.15,
    "dependency": 0.15,
    "failure_chain": 0.30,
    "trigger": 0.15,
    "symptom": 0.10,
    "outcome_quality": 0.15,
}

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is",
    "was", "were", "be", "been", "at", "by", "from", "that", "this", "it",
    "its", "as", "after", "before", "then", "when", "while", "into", "up",
    "out", "not", "no", "new", "all", "any", "each", "every", "became",
    "began", "caused", "causing", "increased", "without",
}

SYNONYMS = {
    "retries": "retry", "retrying": "retry", "retried": "retry", "reissue": "retry",
    "reattempts": "retry", "reattempt": "retry", "retried": "retry",
    "amplification": "amplify", "storm": "amplify",
    "thread": "worker", "threads": "worker", "worker": "worker",
    "pool": "pool", "executor": "worker", "slots": "worker", "slot": "worker",
    "exhausted": "exhaust", "saturation": "exhaust", "saturated": "exhaust",
    "timeout": "timeouts", "timeouts": "timeouts", "timed": "timeouts",
    "latency": "slow", "slowed": "slow", "slow": "slow", "slowness": "slow",
    "gateway": "gateway", "provider": "provider", "vendor": "provider",
    "idempotency": "idempotency", "duplicates": "duplicate",
    "queue": "queue", "backlog": "backlog", "requeue": "requeue",
    "circuit": "circuit", "breaker": "circuit",
    "breakerless": "circuit",
}


def _canonical(word: str) -> str:
    return SYNONYMS.get(word, word)


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9-]+", (text or "").lower())
    return {_canonical(w) for w in words if w not in STOPWORDS and len(w) > 2}


def _token_overlap(a: str, b: str) -> float:
    ta, tb = _tokenize(a), _tokenize(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def _sequence_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


def _compare_chain(a: list[str], b: list[str]) -> float:
    """Compare two failure chains via pairwise token overlap, greedy alignment."""
    if not a or not b:
        return 0.0
    used: set[int] = set()
    scores: list[float] = []
    for step_a in a:
        best = 0.0
        for idx, step_b in enumerate(b):
            if idx in used:
                continue
            score = _token_overlap(step_a, step_b)
            if score > best:
                best = score
                used.add(idx)
        scores.append(best)
    return sum(scores) / len(a)


def compare_fingerprints(
    new: FailureFingerprint,
    stored: FailureFingerprint,
    outcome_boost: float = 0.0,
) -> FingerprintMatch:
    """Score a stored fingerprint against a new one (0..1)."""
    field_scores = {
        "service": _token_overlap(new.service, stored.service),
        "dependency": _token_overlap(new.dependency, stored.dependency),
        "failure_chain": _compare_chain(new.failure_chain, stored.failure_chain),
        "trigger": _token_overlap(new.triggering_change, stored.triggering_change),
        "symptom": _token_overlap(new.symptom, stored.symptom),
        "outcome_quality": outcome_boost,
    }

    score = sum(WEIGHTS[k] * v for k, v in field_scores.items())
    reasons: list[str] = []
    if field_scores["failure_chain"] >= 0.5:
        reasons.append("similar failure chain")
    if field_scores["dependency"] >= 0.5:
        reasons.append(f"same dependency ({stored.dependency})")
    if field_scores["service"] >= 0.5:
        reasons.append(f"same service ({stored.service})")
    if field_scores["trigger"] >= 0.5:
        reasons.append("similar trigger")

    return FingerprintMatch(
        incident_id=stored.incident_id,
        score=round(score, 4),
        field_scores={k: round(v, 4) for k, v in field_scores.items()},
        reasons=reasons,
        stored_fingerprint=stored,
    )


def match_fingerprints(
    new: FailureFingerprint,
    stored_fingerprints: list[FailureFingerprint],
    fix_outcomes: dict[str, float] | None = None,
    min_score: float = 0.15,
    max_results: int = 5,
) -> list[FingerprintMatch]:
    """Rank stored fingerprints against a new one.

    fix_outcomes maps incident_id -> outcome quality (0..1) learned from
    engineer feedback, boosting proven incidents in the ranking.
    """
    fix_outcomes = fix_outcomes or {}
    matches = [
        compare_fingerprints(new, stored, outcome_boost=fix_outcomes.get(stored.incident_id, 0.0))
        for stored in stored_fingerprints
    ]
    matches = [m for m in matches if m.score >= min_score]
    matches.sort(key=lambda m: m.score, reverse=True)
    return matches[:max_results]


def similar_fix_counts(matches: list[FingerprintMatch]) -> tuple[dict[str, int], dict[str, int]]:
    """Aggregate success/failure counts for fixes seen across matches."""
    success: dict[str, int] = {}
    failed: dict[str, int] = {}
    for match in matches:
        for fix in match.stored_fingerprint.successful_fixes:
            key = fix.strip().lower()
            success[key] = success.get(key, 0) + 1
        for fix in match.stored_fingerprint.failed_fixes:
            key = fix.strip().lower()
            failed[key] = failed.get(key, 0) + 1
    return success, failed
