"""Engine tests: stateless isolation, memory citations, confidence honesty."""

from __future__ import annotations

from failureloop.extraction.extractor import _ExtractionLLMResponse
from failureloop.fingerprint import FailureFingerprint
from failureloop.matching.matcher import CandidateJudgeResponse, MatchResult
from failureloop.recommend.engine import (
    EngineDeps,
    EngineLLMResponse,
    recommend_stateless,
    recommend_with_memory,
)
from failureloop.recommend.models import Recommendation
from tests.conftest import FakeLLM, FakeMemory, fingerprint_by_id, make_judge_response


def live_fp() -> FailureFingerprint:
    return FailureFingerprint(
        service="checkout-api",
        symptom="Checkout timeouts",
        dependency="payment-gateway",
        triggering_change="provider slowdown",
        failure_chain=[
            "downstream dependency latency increases",
            "retries multiply load",
            "worker pool saturates",
        ],
    )


def extraction_response(fp: FailureFingerprint) -> _ExtractionLLMResponse:
    return _ExtractionLLMResponse(
        fingerprint=fp,
        extraction_notes=[],
        field_confidence={"dependency": 0.9, "triggering_change": 0.6, "failure_chain": 0.8},
    )


def wire_memory_mode(llm: FakeLLM, memory: FakeMemory, seeded_storage, judge_scores):
    """Script the LLM queues and memory hits for a full memory-mode run."""
    memory.pattern_hits = [
        {"text": "INC-104 ...", "metadata": {"incident_id": "INC-104"}, "score": 0.9},
        {"text": "INC-112 ...", "metadata": {"incident_id": "INC-112"}, "score": 0.7},
    ]
    llm.responses_by_schema[CandidateJudgeResponse] = [
        make_judge_response(judge_scores)
    ]
    llm.responses_by_schema[EngineLLMResponse] = [
        EngineLLMResponse(
            recommendation=Recommendation(
                likely_pattern={"name": "retry amplification", "explanation": "test"},
                confidence=0.95,
                confidence_rationale="scripted high",
            )
        )
    ]
    return EngineDeps(llm=llm, memory=memory, storage=seeded_storage)


def test_stateless_never_touches_memory(seeded_storage):
    llm = FakeLLM(
        {EngineLLMResponse: [EngineLLMResponse(recommendation=Recommendation(confidence=0.8))]}
    )
    memory = FakeMemory()
    output = recommend_stateless(
        live_fp(), "raw incident text", llm,  # type: ignore[arg-type]
        incident_id="T-1", save=False,
    )
    assert output.mode == "stateless"
    assert memory.recall_calls == [] and memory.reflect_calls == []
    rec = output.recommendation
    assert rec.matched_incidents == []
    assert rec.fixes_to_avoid == []
    # Stateless confidence is capped: no history, no strong claims.
    assert rec.confidence <= 0.5
    assert "decision support" in rec.safety_note.lower()


def test_memory_mode_cites_incident_ids(seeded_storage):
    llm = FakeLLM()
    memory = FakeMemory()
    deps = wire_memory_mode(llm, memory, seeded_storage, {"INC-104": 0.9, "INC-112": 0.75})
    deps.llm.responses_by_schema[EngineLLMResponse] = [
        EngineLLMResponse(
            recommendation=Recommendation(
                likely_pattern={"name": "retry amplification", "explanation": "x"},
                recommended_next_steps=[
                    {
                        "step": "Apply bounded retries",
                        "rationale": "worked before",
                        "supporting_incident_ids": ["INC-104"],
                    }
                ],
                fixes_to_avoid=[
                    {"action": "Scale database", "why": "failed", "incident_ids": ["INC-104"]}
                ],
                confidence=0.9,
            )
        )
    ]
    output = recommend_with_memory(live_fp(), "raw text", deps, incident_id="T-2", save=False)
    rec = output.recommendation
    # The engine reflected over memory and the LLM saw the matches.
    assert memory.reflect_calls, "memory mode must call reflect"
    steps_user_prompt = deps.llm.json_calls[-1]["user"]
    assert "INC-104" in steps_user_prompt
    assert rec.matched_incidents, "engine injects matched refs"
    assert rec.matched_incidents[0].incident_id == "INC-104"
    assert rec.recommended_next_steps[0].supporting_incident_ids == ["INC-104"]
    assert rec.fixes_to_avoid[0].incident_ids == ["INC-104"]
    assert rec.matched_incidents[0].similarity > 0


def test_confidence_clamped_when_matches_weak(seeded_storage):
    llm = FakeLLM()
    memory = FakeMemory()
    # Judge says both matches are causally weak.
    deps = wire_memory_mode(llm, memory, seeded_storage, {"INC-104": 0.2, "INC-112": 0.3})
    deps.llm.responses_by_schema[EngineLLMResponse] = [
        EngineLLMResponse(recommendation=Recommendation(confidence=0.95))
    ]
    output = recommend_with_memory(live_fp(), "raw text", deps, incident_id="T-3", save=False)
    rec = output.recommendation
    assert rec.confidence <= 0.35, "no strong matches: confidence must be clamped low"
    assert "clamp" in rec.confidence_rationale.lower()


def test_reflection_included_in_prompt(seeded_storage):
    llm = FakeLLM()
    memory = FakeMemory()
    memory.reflection = "Past incidents show bounded retries worked; database scaling failed."
    deps = wire_memory_mode(llm, memory, seeded_storage, {"INC-104": 0.9})
    recommend_with_memory(live_fp(), "raw text", deps, incident_id="T-4", save=False)
    prompt = deps.llm.json_calls[-1]["user"]
    assert "bounded retries worked" in prompt
