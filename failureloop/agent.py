"""Agent controller: orchestrates extraction -> memory -> matching ->
recommendation -> feedback -> learning.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from failureloop.config import Config, get_config
from failureloop.data import HISTORICAL_INCIDENTS
from failureloop.extractor import FingerprintExtractor
from failureloop.fingerprint import FailureFingerprint, FingerprintMatch, Recommendation
from failureloop.matcher import match_fingerprints
from failureloop.memory import MemoryLayer
from failureloop.recommender import Recommender
from failureloop.storage import Storage


@dataclass
class AnalysisResult:
    incident_id: str
    fingerprint: FailureFingerprint
    matches: list[FingerprintMatch]
    recommendation: Recommendation
    memory_mode: str
    recall_count: int


class FailureLoopAgent:
    """Top-level orchestrator for the incident workflow."""

    def __init__(self, config: Config | None = None):
        self.config = config or get_config()
        self.storage = Storage(self.config.db_path)
        self.memory = MemoryLayer(self.config)
        self.extractor = FingerprintExtractor(self.config)
        self.recommender = Recommender(self.config)

    # ------------------------------------------------------------------ intake

    def analyze(
        self,
        raw_text: str,
        incident_id: str = "",
        mode: str = "memory",
    ) -> AnalysisResult:
        """Full pipeline: extract fingerprint, match, recommend, persist."""
        # 1. Extract structured fingerprint.
        fingerprint = self.extractor.extract(raw_text, incident_id=incident_id)
        if not fingerprint.incident_id:
            fingerprint.incident_id = self._next_id()

        # 1b. Preserve confirmed outcomes if this incident was analyzed before
        # (re-analysis must not wipe engineer-confirmed fixes / root cause).
        existing = self.storage.load_incident(fingerprint.incident_id)
        if existing is not None:
            prev_fp, _ = existing
            for fix in prev_fp.successful_fixes:
                if fix not in fingerprint.successful_fixes:
                    fingerprint.successful_fixes.append(fix)
            for fix in prev_fp.failed_fixes:
                if fix not in fingerprint.failed_fixes:
                    fingerprint.failed_fixes.append(fix)
            if not fingerprint.root_cause and prev_fp.root_cause:
                fingerprint.root_cause = prev_fp.root_cause

        # 2. Persist the incident.
        self.storage.save_incident(fingerprint, raw_text)

        # 3. Match against stored fingerprints.
        matches = self.find_matches(fingerprint)

        # 4. Generate recommendation (memory or stateless mode).
        rec_mode = mode
        recommendation = self.recommender.generate(fingerprint, matches, mode=rec_mode)

        # 5. Persist recommendation.
        self.storage.save_recommendation(fingerprint.incident_id, recommendation, rec_mode)

        return AnalysisResult(
            incident_id=fingerprint.incident_id,
            fingerprint=fingerprint,
            matches=matches,
            recommendation=recommendation,
            memory_mode=self.memory.mode,
            recall_count=len(matches),
        )

    # -------------------------------------------------------------- matching

    def find_matches(
        self,
        fingerprint: FailureFingerprint,
        exclude_self: bool = True,
    ) -> list[FingerprintMatch]:
        stored = self.storage.all_fingerprints()
        if exclude_self:
            stored = [fp for fp in stored if fp.incident_id != fingerprint.incident_id]
        outcome_quality = self.storage.feedback_outcome_quality()
        return match_fingerprints(fingerprint, stored, fix_outcomes=outcome_quality)

    # ----------------------------------------------------------- memory ops

    def load_historical(self) -> int:
        """Load the synthetic historical postmortems into SQLite + memory."""
        count = 0
        for incident in HISTORICAL_INCIDENTS:
            fp = incident["fingerprint"].model_copy(deep=True)
            fp.incident_id = incident["incident_id"]
            raw = incident["raw_text"]
            self.storage.save_incident(fp, raw, status="resolved")
            self.memory.retain_incident(fp, raw_text=raw)
            count += 1
        return count

    def memory_stats(self) -> dict:
        return {
            "mode": self.memory.mode,
            "sqlite_incidents": self.storage.incident_count(),
            "hindsight_memories": self.memory.memory_count(),
        }

    # ------------------------------------------------------------- feedback

    def record_feedback(
        self,
        incident_id: str,
        outcome: str,
        feedback_text: str = "",
        confirmed_root_cause: str = "",
        confirmed_fix: str = "",
    ) -> dict:
        """Record engineer feedback and turn it into a learning event."""
        loaded = self.storage.load_incident(incident_id)
        if loaded is None:
            raise ValueError(f"Unknown incident: {incident_id}")
        fp, _raw = loaded

        self.storage.save_feedback(incident_id, None, outcome, feedback_text)

        # Update the fingerprint with confirmed reality.
        if confirmed_root_cause:
            fp.root_cause = confirmed_root_cause
        if confirmed_fix:
            if outcome in ("worked", "partially_worked"):
                if confirmed_fix not in fp.successful_fixes:
                    fp.successful_fixes.append(confirmed_fix)
                if confirmed_fix in fp.failed_fixes:
                    fp.failed_fixes.remove(confirmed_fix)
            elif outcome == "failed":
                if confirmed_fix not in fp.failed_fixes:
                    fp.failed_fixes.append(confirmed_fix)
                if confirmed_fix in fp.successful_fixes:
                    fp.successful_fixes.remove(confirmed_fix)
        self.storage.save_incident(fp, _raw, status="resolved")

        # Retain the learning event so future recall ranks this fix.
        memory_text = (
            f"Feedback for incident {incident_id}: the recommendation {outcome}. "
            f"{feedback_text} "
            + (f"Confirmed root cause: {confirmed_root_cause}. " if confirmed_root_cause else "")
            + (
                f"Fix '{confirmed_fix}' should receive higher priority for future incidents "
                f"with the same causal chain."
                if confirmed_fix and outcome in ("worked", "partially_worked")
                else (
                    f"Fix '{confirmed_fix}' failed and should be avoided for similar incidents."
                    if confirmed_fix
                    else ""
                )
            )
        )
        self.memory.retain_incident(
            fp, outcome=outcome, feedback_text=memory_text
        )

        return {
            "incident_id": incident_id,
            "outcome": outcome,
            "updated_fingerprint": fp,
            "memory_event": memory_text,
        }

    # -------------------------------------------------------------- internal

    def close(self) -> None:
        """Close the SQLite connection (idempotent)."""
        try:
            self.storage.close()
        except Exception:
            pass

    def _next_id(self) -> str:
        return f"INC-{self.storage.next_incident_number()}"
