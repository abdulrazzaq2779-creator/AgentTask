"""Outcome-aware fix ranking (Stage 2 D).

Aggregates fixes across matched incidents:
- successful fixes ranked by incident count, match strength, recency, resolution speed;
- failed fixes surfaced as "tried before, did NOT work" with evidence;
- conflicting evidence kept VISIBLE (worked once, failed once) - never averaged away.
Every ranked fix carries its supporting incident IDs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from failureloop.fingerprint import FailureFingerprint
from failureloop.matching.matcher import MatchResult

logger = logging.getLogger("failureloop.fix_ranker")

# Normalization groups fixes that are the same action with different wording.
SIMILAR_FIX_THRESHOLD = 0.72
# How strongly each factor influences the success ranking (relative weights).
RANK_WEIGHTS = {
    "incident_count": 0.40,
    "match_strength": 0.25,
    "recency": 0.20,
    "resolution_speed": 0.15,
}


def _normalize_key(fix_text: str) -> str:
    return " ".join((fix_text or "").lower().split())


def _texts_similar(a: str, b: str) -> bool:
    return SequenceMatcher(None, _normalize_key(a), _normalize_key(b)).ratio() >= SIMILAR_FIX_THRESHOLD


@dataclass
class RankedFix:
    """One fix aggregated across incidents, with its evidence trail."""

    action: str
    supporting_incident_ids: list[str] = field(default_factory=list)
    incident_count: int = 0
    match_strength: float = 0.0
    recency: float = 0.0
    resolution_speed: float = 0.0
    rank_score: float = 0.0
    variants: list[str] = field(default_factory=list)


@dataclass
class ConflictedFix:
    """A fix with conflicting evidence: worked somewhere, failed elsewhere."""

    action: str
    worked_in: list[str] = field(default_factory=list)
    failed_in: list[str] = field(default_factory=list)
    variants: list[str] = field(default_factory=list)


@dataclass
class RankedFixes:
    proven_fixes: list[RankedFix] = field(default_factory=list)
    failed_fixes: list[RankedFix] = field(default_factory=list)
    conflicting_fixes: list[ConflictedFix] = field(default_factory=list)

    @property
    def has_evidence(self) -> bool:
        return bool(self.proven_fixes or self.failed_fixes or self.conflicting_fixes)


class _FixAccumulator:
    """Groups fix variants and accumulates evidence per normalized fix."""

    def __init__(self) -> None:
        self.canonical: dict[str, dict] = {}

    def add(self, fix_text: str, incident_id: str, outcome: str, match: MatchResult) -> None:
        text = (fix_text or "").strip()
        if not text:
            return
        key = None
        for existing_key in self.canonical:
            if _texts_similar(text, existing_key):
                key = existing_key
                break
        if key is None:
            key = _normalize_key(text)
            self.canonical[key] = {
                "canonical_text": text,
                "variants": [text],
                "incidents": set(),
                "strength_sum": 0.0,
                "recencies": [],
                "resolution_minutes": [],
            }
        entry = self.canonical[key]
        if text not in entry["variants"]:
            entry["variants"].append(text)
        if incident_id not in entry["incidents"]:
            entry["incidents"].add(incident_id)
            entry["strength_sum"] += match.total
            entry["recencies"].append(match.components.get("recency", 0.5))
            res_minutes = match.fingerprint.resolution_time_minutes if match.fingerprint else None
            entry["resolution_minutes"].append(res_minutes)

    def ranked(self) -> list[RankedFix]:
        ranked: list[RankedFix] = []
        for entry in self.canonical.values():
            n = len(entry["incidents"])
            strength = entry["strength_sum"] / n
            recency = sum(entry["recencies"]) / len(entry["recencies"]) if entry["recencies"] else 0.5
            minutes = [m for m in entry["resolution_minutes"] if m]
            if minutes:
                avg_min = sum(minutes) / len(minutes)
                speed = max(0.0, 1.0 - min(avg_min / 120.0, 1.0))  # <=1h great, >=2h poor
            else:
                speed = 0.5
            score = (
                RANK_WEIGHTS["incident_count"] * min(n / 3.0, 1.0)
                + RANK_WEIGHTS["match_strength"] * strength
                + RANK_WEIGHTS["recency"] * recency
                + RANK_WEIGHTS["resolution_speed"] * speed
            )
            ranked.append(
                RankedFix(
                    action=entry["canonical_text"],
                    supporting_incident_ids=sorted(entry["incidents"]),
                    incident_count=n,
                    match_strength=round(strength, 3),
                    recency=round(recency, 3),
                    resolution_speed=round(speed, 3),
                    rank_score=round(score, 4),
                    variants=entry["variants"],
                )
            )
        ranked.sort(key=lambda f: f.rank_score, reverse=True)
        return ranked


def rank_fixes(matches: list[MatchResult]) -> RankedFixes:
    """Aggregate fixes across matches. Partial matches contribute nothing."""
    proven_acc = _FixAccumulator()
    failed_acc = _FixAccumulator()
    worked_by_key: dict[str, list[str]] = {}
    failed_by_key: dict[str, list[str]] = {}

    for match in matches:
        if match.match_type == "partial_match":
            continue  # weak evidence must not drive fix recommendations
        fp = match.fingerprint
        if fp is None:
            continue
        for fix in fp.successful_fixes:
            proven_acc.add(fix, match.incident_id, "success", match)
        for fix in fp.failed_fixes:
            failed_acc.add(fix, match.incident_id, "failed", match)
        # Conflicts: same fix (normalized) present in both lists of one incident,
        # or seen as success in one incident and failure in another.
        for fix in fp.successful_fixes:
            key = _normalize_key(fix)
            worked_by_key.setdefault(key, []).append(match.incident_id)
        for fix in fp.failed_fixes:
            key = _normalize_key(fix)
            failed_by_key.setdefault(key, []).append(match.incident_id)

    proven = proven_acc.ranked()
    failed = failed_acc.ranked()

    # Cross-check conflicts by matching variant texts between the two lists.
    conflicting: list[ConflictedFix] = []
    for fix in proven:
        for other in failed:
            if _texts_similar(fix.action, other.action):
                conflicting.append(
                    ConflictedFix(
                        action=fix.action,
                        worked_in=fix.supporting_incident_ids,
                        failed_in=other.supporting_incident_ids,
                        variants=sorted(set(fix.variants) | set(other.variants)),
                    )
                )
    conflict_actions = {c.action.lower() for c in conflicting}
    proven = [f for f in proven if f.action.lower() not in conflict_actions]
    failed = [f for f in failed if f.action.lower() not in conflict_actions]

    result = RankedFixes(proven_fixes=proven, failed_fixes=failed, conflicting_fixes=conflicting)
    logger.info(
        "ranked fixes: %d proven, %d failed, %d conflicting",
        len(result.proven_fixes),
        len(result.failed_fixes),
        len(result.conflicting_fixes),
    )
    return result
