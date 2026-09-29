"""Stage 2 fingerprint extraction.

Turns messy incident text (alert dump, Slack thread, postmortem) into the
Stage 1 FailureFingerprint plus extraction notes and per-field confidence.

Rules enforced here (belt and braces with the prompt):
- Fields the text does not support stay empty/null - never invented.
- failure_chain is an ordered ABSTRACT causal chain, not copied error strings.
- New live incidents legitimately have unknown root cause and fixes.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from failureloop.fingerprint import FailureFingerprint
from failureloop.llm.client import LLMClient
from failureloop.llm.prompts import load_prompt

logger = logging.getLogger("failureloop.extraction")

VALID_OUTCOMES = {"success", "failed", "partial", "unknown"}
OUTCOME_SYNONYMS = {
    "worked": "success",
    "resolved": "success",
    "successful": "success",
    "fixed": "success",
    "did_not_work": "failed",
    "failed_fix": "failed",
    "no_effect": "failed",
    "temporary": "partial",
    "partially": "partial",
}
MAX_CHAIN_STEPS = 8
SEVERITY_RE = re.compile(r"^SEV-[1-4]$", re.IGNORECASE)
SEVERITY_SEARCH_RE = re.compile(r"SEV-[1-4]", re.IGNORECASE)


class ExtractionOutput(BaseModel):
    """Full extractor output: fingerprint + transparency metadata."""

    fingerprint: FailureFingerprint
    extraction_notes: list[str] = Field(default_factory=list)
    field_confidence: dict[str, float] = Field(default_factory=dict)


class _ExtractionLLMResponse(BaseModel):
    """Schema the LLM must return (wraps the Stage 1 fingerprint)."""

    fingerprint: FailureFingerprint
    extraction_notes: list[str] = Field(default_factory=list)
    field_confidence: dict[str, float] = Field(default_factory=dict)

    model_config = {"extra": "ignore"}


def _coerce_fix_outcomes(fp: dict[str, Any]) -> None:
    """Coerce attempted_fix fields to valid values pre-validation.

    The prompt tells the model to leave unknown fields null, so nested fix
    fields arrive as null too (e.g. notes: null) and the Stage 1 Literal
    outcome may be a synonym like 'worked'. Normalize all of it here so it
    sanitizes cleanly instead of triggering the repair loop.
    """
    fixes = fp.get("attempted_fixes")
    if not isinstance(fixes, list):
        return
    coerced: list[dict[str, Any]] = []
    for fix in fixes:
        if not isinstance(fix, dict):
            continue
        fix["action"] = str(fix.get("action") or "").strip()
        fix["notes"] = str(fix.get("notes") or "")
        raw = str(fix.get("outcome") or "unknown").strip().lower()
        fix["outcome"] = OUTCOME_SYNONYMS.get(raw, raw if raw in VALID_OUTCOMES else "unknown")
        coerced.append(fix)
    fp["attempted_fixes"] = coerced
    # list[str] fields: drop null/None elements and non-strings.
    for key in ("successful_fixes", "failed_fixes"):
        values = fp.get(key)
        if isinstance(values, list):
            fp[key] = [str(v) for v in values if v]


def _coerce_none(payload: dict[str, Any]) -> dict[str, Any]:
    """Replace nulls with schema defaults so 'leave unknown null' validates.

    Keeps the Stage 1 FailureFingerprint (str fields defaulting to "") untouched.
    """
    fp = dict(payload.get("fingerprint") or {})
    for key, value in list(fp.items()):
        if value is None:
            if key in ("failure_chain", "attempted_fixes", "successful_fixes", "failed_fixes"):
                fp[key] = []
            elif key == "resolution_time_minutes":
                fp[key] = None  # genuinely Optional
            else:
                fp[key] = ""
    _coerce_fix_outcomes(fp)
    payload["fingerprint"] = fp

    notes = payload.get("extraction_notes")
    payload["extraction_notes"] = [str(n) for n in notes if n] if isinstance(notes, list) else []

    conf = payload.get("field_confidence")
    cleaned: dict[str, float] = {}
    if isinstance(conf, dict):
        for key in ("dependency", "triggering_change", "failure_chain"):
            try:
                cleaned[key] = max(0.0, min(1.0, float(conf.get(key, 0.5))))
            except (TypeError, ValueError):
                cleaned[key] = 0.5
    payload["field_confidence"] = cleaned
    return payload


def _sanitize_fingerprint(fp: FailureFingerprint, incident_id: str | None) -> FailureFingerprint:
    """Sanitize obviously bad values after validation."""
    fp.failure_chain = [step.strip()[:200] for step in fp.failure_chain if step and step.strip()][
        :MAX_CHAIN_STEPS
    ]
    for fix in fp.attempted_fixes:
        outcome = (fix.outcome or "unknown").strip().lower()
        fix.outcome = OUTCOME_SYNONYMS.get(outcome, outcome if outcome in VALID_OUTCOMES else "unknown")
    # successful/failed fix lists must not contradict attempted outcomes absence.
    fp.successful_fixes = [f.strip()[:200] for f in fp.successful_fixes if f and f.strip()]
    fp.failed_fixes = [f.strip()[:200] for f in fp.failed_fixes if f and f.strip()]
    if fp.severity and not SEVERITY_RE.match(fp.severity):
        match = SEVERITY_SEARCH_RE.search(fp.severity)
        fp.severity = match.group(0).upper() if match else ""
    fp.incident_id = incident_id or fp.incident_id or ""
    for field_name in ("service", "dependency", "symptom", "root_cause"):
        value = getattr(fp, field_name)
        setattr(fp, field_name, (value or "").strip()[:300])
    return fp


class FingerprintExtractor:
    """LLM-backed extractor. One dependency: the shared LLMClient."""

    def __init__(self, llm: LLMClient):
        self.llm = llm
        self._system_prompt = load_prompt("extract_fingerprint")

    def extract(self, raw_text: str, incident_id: str | None = None) -> ExtractionOutput:
        if not raw_text or not raw_text.strip():
            raise ValueError("raw_text must be non-empty")
        user_prompt = (
            "Extract the failure fingerprint from this incident text. "
            "Unknown fields must be null. failure_chain must be abstract causal steps.\n\n"
            f"INCIDENT_ID: {incident_id or ''}\n\n"
            f"INCIDENT TEXT:\n{raw_text.strip()[:12000]}"
        )
        response = self.llm.complete_json(self._system_prompt, user_prompt, _ExtractionLLMResponse)
        payload = _coerce_none(response.model_dump())
        parsed = _ExtractionLLMResponse.model_validate(payload)
        fingerprint = _sanitize_fingerprint(parsed.fingerprint, incident_id)
        logger.info(
            "extracted incident=%s chain_steps=%d confidence=%s",
            fingerprint.incident_id or "?",
            len(fingerprint.failure_chain),
            parsed.field_confidence,
        )
        return ExtractionOutput(
            fingerprint=fingerprint,
            extraction_notes=parsed.extraction_notes,
            field_confidence=parsed.field_confidence,
        )

    def extract_fingerprint(self, raw_text: str, incident_id: str | None = None) -> FailureFingerprint:
        return self.extract(raw_text, incident_id).fingerprint


def extract_fingerprint(
    raw_text: str,
    incident_id: str | None = None,
    llm: LLMClient | None = None,
) -> FailureFingerprint:
    """Module-level convenience per the Stage 2 spec."""
    if llm is None:
        from failureloop.config import get_config

        llm = LLMClient(get_config())
    return FingerprintExtractor(llm).extract_fingerprint(raw_text, incident_id)
