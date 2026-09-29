"""Extractor tests: unknown fields stay null, nothing is invented."""

from __future__ import annotations

import pytest

from failureloop.extraction.extractor import FingerprintExtractor
from failureloop.fingerprint import FailureFingerprint
from tests.conftest import FakeLLM

from pydantic import ValidationError


def extraction_schema():
    from failureloop.extraction.extractor import _ExtractionLLMResponse

    return _ExtractionLLMResponse


def test_live_incident_root_cause_stays_null():
    """A new live incident has no known root cause - must not be invented."""
    llm_response = extraction_schema()(
        fingerprint=FailureFingerprint(
            service="checkout-api",
            symptom="Checkout requests timing out",
            dependency="payment-gateway",
            failure_chain=[
                "downstream dependency latency increases",
                "client requests time out",
                "retries multiply load",
                "worker pool saturates",
            ],
            root_cause="",
            successful_fixes=[],
            failed_fixes=[],
        ),
        extraction_notes=["root_cause left null: text does not state a confirmed cause"],
        field_confidence={"dependency": 0.9, "triggering_change": 0.4, "failure_chain": 0.85},
    )
    llm = FakeLLM({extraction_schema(): [llm_response]})
    extractor = FingerprintExtractor(llm)  # type: ignore[arg-type]
    result = extractor.extract(
        "checkout is timing out, provider degraded, workers at 100%",
        incident_id="INC-900",
    )
    assert result.fingerprint.root_cause == ""
    assert result.fingerprint.incident_id == "INC-900"
    assert result.field_confidence["dependency"] == 0.9
    assert any("root_cause" in note for note in result.extraction_notes)


def test_extractor_requires_nonempty_text():
    llm = FakeLLM()
    extractor = FingerprintExtractor(llm)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        extractor.extract("   ")


def test_sanitizer_normalizes_bad_outcome_and_severity():
    from failureloop.fingerprint import AttemptedFix

    # Construct with valid defaults, then inject invalid values post-validation
    # the way a sloppy LLM response would slip them in.
    fix_worked = AttemptedFix(action="scaled db")
    fix_worked.outcome = "worked"
    fix_banana = AttemptedFix(action="restarted")
    fix_banana.outcome = "banana"

    llm_response = extraction_schema()(
        fingerprint=FailureFingerprint(service="svc", severity="severity: sev-2"),
        field_confidence={"dependency": 0.5, "triggering_change": 0.5, "failure_chain": 0.5},
    )
    llm_response.fingerprint.attempted_fixes = [fix_worked, fix_banana]
    llm = FakeLLM({extraction_schema(): [llm_response]})
    extractor = FingerprintExtractor(llm)  # type: ignore[arg-type]
    result = extractor.extract("text")
    outcomes = [fix.outcome for fix in result.fingerprint.attempted_fixes]
    assert outcomes == ["success", "unknown"]
    assert result.fingerprint.severity == "SEV-2"


def test_chain_is_capped():
    steps = [f"step {i} causes the next thing to break badly" for i in range(20)]
    llm_response = extraction_schema()(
        fingerprint=FailureFingerprint(service="svc", failure_chain=steps),
        field_confidence={"dependency": 0.5, "triggering_change": 0.5, "failure_chain": 0.5},
    )
    llm = FakeLLM({extraction_schema(): [llm_response]})
    extractor = FingerprintExtractor(llm)  # type: ignore[arg-type]
    result = extractor.extract("text")
    assert len(result.fingerprint.failure_chain) <= 8
