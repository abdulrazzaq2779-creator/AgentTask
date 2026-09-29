"""Memory layer: Hindsight Cloud (retain / recall / reflect) with a local
keyword fallback so the demo still runs without credentials.

All FailureLoop memory flows through this module.
"""

from __future__ import annotations

import json
import re
from typing import Any

from failureloop.config import Config
from failureloop.fingerprint import FailureFingerprint


class MemoryLayer:
    """Facade over Hindsight; degrades to a local store when unavailable."""

    def __init__(self, config: Config):
        self.config = config
        self._client = None
        self._hindsight_enabled = False
        self._local_memories: list[dict] = []

        if config.has_hindsight:
            self._client = self._make_client()

    def _make_client(self) -> Any | None:
        try:
            from hindsight_client import Hindsight

            client = Hindsight(
                base_url=self.config.hindsight_base_url,
                api_key=self.config.hindsight_api_key,
            )
            self._hindsight_enabled = True
            return client
        except Exception as exc:  # pragma: no cover - depends on optional package
            print(f"[FailureLoop] Hindsight unavailable ({exc}); using local fallback.")
            return None

    @property
    def mode(self) -> str:
        return "hindsight" if self._hindsight_enabled else "local-fallback"

    # ------------------------------------------------------------------ retain

    def retain_incident(
        self,
        fingerprint: FailureFingerprint,
        raw_text: str = "",
        outcome: str = "",
        feedback_text: str = "",
    ) -> None:
        """Retain an incident (or a learning event) into memory."""
        content = self._incident_memory_text(fingerprint, raw_text, outcome, feedback_text)
        metadata = {
            "incident_id": fingerprint.incident_id,
            "service": fingerprint.service,
            "dependency": fingerprint.dependency,
            "root_cause": fingerprint.root_cause,
            "outcome": outcome,
            "type": "feedback" if outcome else "incident",
        }

        if self._hindsight_enabled and self._client is not None:
            try:
                self._client.retain(
                    bank_id=self.config.hindsight_bank_id,
                    content=content,
                    context="production incident history",
                    metadata=metadata,
                )
                return
            except Exception as exc:
                print(f"[FailureLoop] Hindsight retain failed ({exc}); keeping locally.")

        self._local_memories.append({"content": content, "metadata": metadata})

    # ------------------------------------------------------------------ recall

    def recall_similar(self, fingerprint: FailureFingerprint, max_results: int = 5) -> list[dict]:
        """Recall memories relevant to a new incident fingerprint."""
        query = self._recall_query(fingerprint)

        if self._hindsight_enabled and self._client is not None:
            try:
                response = self._client.recall(
                    bank_id=self.config.hindsight_bank_id,
                    query=query,
                    max_results=max_results,
                )
                return self._normalize_recall(response)
            except Exception as exc:
                print(f"[FailureLoop] Hindsight recall failed ({exc}); using local fallback.")

        return self._local_recall(query, max_results)

    def reflect(self, query: str) -> str:
        """Reason over retained memories; returns a text analysis."""
        if self._hindsight_enabled and self._client is not None:
            try:
                response = self._client.reflect(
                    bank_id=self.config.hindsight_bank_id,
                    query=query,
                )
                return str(response)
            except Exception as exc:
                print(f"[FailureLoop] Hindsight reflect failed ({exc}); using local fallback.")
        return ""

    def seed_local_from_storage(self, storage) -> int:
        """Load stored incidents into the LOCAL fallback memory.

        Without a Hindsight key the in-process memory would be empty in every
        new process, making recall useless. SQLite is persistent, so in
        local-fallback mode it doubles as the memory corpus. No-op when
        Hindsight is connected (it stays the primary memory layer).
        """
        if self._hindsight_enabled:
            return 0
        count = 0
        for fp in storage.all_fingerprints():
            if any(
                m["metadata"].get("incident_id") == fp.incident_id
                for m in self._local_memories
            ):
                continue
            content = self._incident_memory_text(fp, raw_text="", outcome="", feedback_text="")
            self._local_memories.append(
                {
                    "content": content,
                    "metadata": {
                        "incident_id": fp.incident_id,
                        "service": fp.service,
                        "dependency": fp.dependency,
                        "root_cause": fp.root_cause,
                        "outcome": "",
                        "type": "incident",
                    },
                }
            )
            count += 1
        return count

    def recall_text(self, query: str, max_results: int = 10) -> list[dict]:
        """Free-text recall for arbitrary queries (used by the Stage 2 matcher).

        Never raises; returns [] when Hindsight is unavailable. Items are
        normalized to {"text": str, "metadata": dict, "score": float}.
        """
        if self._hindsight_enabled and self._client is not None:
            try:
                response = self._client.recall(
                    bank_id=self.config.hindsight_bank_id,
                    query=query,
                    max_results=max_results,
                )
                return self._normalize_recall(response)
            except Exception as exc:
                print(f"[FailureLoop] Hindsight recall failed ({exc}).")
            return []
        return self._local_recall(query, max_results)

    def reflect_text(self, query: str) -> str:
        """Reflect with normalization: always plain text, never raises."""
        raw = self.reflect(query)
        if not raw:
            return ""
        try:
            decoded = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return raw
        if isinstance(decoded, dict):
            for key in ("text", "answer", "response", "result"):
                if decoded.get(key):
                    return str(decoded[key])
        return raw

    # ------------------------------------------------------------------ helpers

    def _incident_memory_text(
        self,
        fingerprint: FailureFingerprint,
        raw_text: str,
        outcome: str,
        feedback_text: str,
    ) -> str:
        lines = [f"Historical incident {fingerprint.incident_id}."]
        if fingerprint.service:
            lines.append(f"Service: {fingerprint.service}.")
        if fingerprint.symptom:
            lines.append(f"Symptom: {fingerprint.symptom}.")
        if fingerprint.dependency:
            lines.append(f"Downstream dependency: {fingerprint.dependency}.")
        if fingerprint.triggering_change:
            lines.append(f"Trigger: {fingerprint.triggering_change}.")
        if fingerprint.failure_chain:
            lines.append("Failure chain: " + " -> ".join(fingerprint.failure_chain) + ".")
        if fingerprint.root_cause:
            lines.append(f"Root cause: {fingerprint.root_cause}.")
        if fingerprint.successful_fixes:
            lines.append("Fixes that worked: " + "; ".join(fingerprint.successful_fixes) + ".")
        if fingerprint.failed_fixes:
            lines.append(
                "Fixes that were attempted and failed: " + "; ".join(fingerprint.failed_fixes) + "."
            )
        if fingerprint.preventive_action:
            lines.append(f"Prevention: {fingerprint.preventive_action}.")
        if raw_text:
            lines.append(f"Original report: {raw_text}")
        if outcome:
            lines.append(f"Engineer feedback: the recommendation {outcome}. {feedback_text}")
            lines.append(
                "This outcome should influence the priority of similar fixes for future "
                "incidents with the same causal chain."
            )
        return "\n".join(lines)

    def _recall_query(self, fingerprint: FailureFingerprint) -> str:
        parts = [
            "Find historical incidents involving:",
            f"- Service: {fingerprint.service or 'unknown'}",
            f"- Symptom: {fingerprint.symptom or 'unknown'}",
            f"- Error type: {fingerprint.error_type or 'unknown'}",
            f"- Triggering change: {fingerprint.triggering_change or 'unknown'}",
            f"- Dependency: {fingerprint.dependency or 'unknown'}",
        ]
        if fingerprint.failure_chain:
            parts.append("- Failure chain: " + " -> ".join(fingerprint.failure_chain))
        parts.append(
            "Prioritize incidents with similar causal chains and root causes, "
            "even if their error messages are different. Include which fixes "
            "succeeded and which failed."
        )
        return "\n".join(parts)

    def _normalize_recall(self, response: Any) -> list[dict]:
        items: list[Any] = []
        if isinstance(response, dict):
            items = response.get("results") or response.get("memories") or []
        elif isinstance(response, list):
            items = response

        normalized: list[dict] = []
        for item in items:
            if isinstance(item, dict):
                normalized.append(
                    {
                        "text": str(item.get("text") or item.get("content") or ""),
                        "metadata": item.get("metadata") or {},
                        "score": float(item.get("score") or 0.0),
                    }
                )
            else:
                normalized.append({"text": str(item), "metadata": {}, "score": 0.0})
        return normalized

    def _local_recall(self, query: str, max_results: int) -> list[dict]:
        """Keyword-overlap scoring against locally retained memories."""
        stop = {
            "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
            "is", "was", "were", "be", "been", "at", "by", "from", "that", "this",
            "find", "historical", "incidents", "involving", "service", "unknown",
            "include", "which", "prioritize", "similar", "even", "their", "are",
        }
        words = {w for w in re.findall(r"[a-z0-9-]+", query.lower()) if w not in stop and len(w) > 2}

        scored: list[dict] = []
        for memory in self._local_memories:
            text = memory["content"].lower()
            overlap = sum(1 for w in words if w in text)
            scored.append({**memory, "score": overlap / max(len(words), 1)})

        scored.sort(key=lambda m: m["score"], reverse=True)
        return [m for m in scored if m["score"] > 0][:max_results]

    def memory_count(self) -> int:
        if self._hindsight_enabled and self._client is not None:
            try:
                bank = self._client.get_bank(bank_id=self.config.hindsight_bank_id)
                if isinstance(bank, dict):
                    for key in ("memory_count", "total_memories", "fact_count"):
                        if key in bank:
                            return int(bank[key])
                return -1  # connected, count unknown
            except Exception:
                return -1
        return len(self._local_memories)
