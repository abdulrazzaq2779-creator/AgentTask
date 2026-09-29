"""Matcher tests: pattern > keyword, near-miss flagging, cold start."""

from __future__ import annotations

from failureloop.fingerprint import FailureFingerprint
from failureloop.matching.matcher import (
    CandidateJudgeResponse,
    MatchingOutput,
    find_similar_incidents,
    MIN_CONFIDENT_SCORE,
)
from tests.conftest import FakeLLM, FakeMemory, fingerprint_by_id, make_judge_response

from pydantic import ValidationError


def new_incident_fp() -> FailureFingerprint:
    """New incident with the retry-amplification pattern but different words."""
    return FailureFingerprint(
        service="basket-service",
        symptom="Buy button spinning forever",
        error_type="HTTP 500",
        dependency="card-processor",
        triggering_change="vendor capacity event",
        failure_chain=[
            "downstream dependency latency increases",
            "client requests time out",
            "retries multiply load",
            "worker pool saturates",
        ],
    )


def idempotency_fp() -> FailureFingerprint:
    """INC-124-style: same service/dependency, totally different mechanism."""
    return FailureFingerprint(
        service="checkout-api",
        symptom="Duplicate charges on orders",
        error_type="Data integrity",
        dependency="payment-gateway",
        triggering_change="user double submit",
        failure_chain=["duplicate request accepted", "no idempotency check", "charge executed twice"],
    )


def test_pattern_match_beats_keyword_only_match(seeded_storage):
    """Causal similarity dominates: the pattern sibling wins over the
    same-service/same-dependency near-miss."""
    inc104 = fingerprint_by_id("INC-104")
    inc124 = fingerprint_by_id("INC-124")
    memory = FakeMemory()
    # Both incidents recalled via the pattern query; only INC-124 via surface.
    memory.pattern_hits = [
        {"text": "INC-104 ...", "metadata": {"incident_id": "INC-104"}, "score": 0.8},
        {"text": "INC-124 ...", "metadata": {"incident_id": "INC-124"}, "score": 0.3},
    ]
    memory.surface_hits = [
        {"text": "INC-124 ...", "metadata": {"incident_id": "INC-124"}, "score": 0.9},
    ]
    llm = FakeLLM(
        {CandidateJudgeResponse: [make_judge_response({"INC-104": 0.92, "INC-124": 0.15})]}
    )
    output = find_similar_incidents(
        new_incident_fp(),
        top_k=5,
        memory=memory,
        storage=seeded_storage,
        llm=llm,
    )
    assert not output.cold_start
    assert output.matches[0].incident_id == "INC-104"
    assert output.matches[0].components["causal_chain_similarity"] > 0.9
    assert output.matches[0].total > MIN_CONFIDENT_SCORE


def test_near_miss_flagged_partial(seeded_storage):
    """INC-124 shares the surface symptom family but has a different mechanism:
    must be partial_match and must not count as a confident match."""
    memory = FakeMemory()
    memory.pattern_hits = [
        {"text": "INC-124 duplicate charges", "metadata": {"incident_id": "INC-124"}, "score": 0.4},
    ]
    memory.surface_hits = [
        {"text": "INC-124 duplicate charges", "metadata": {"incident_id": "INC-124"}, "score": 0.9},
    ]
    llm = FakeLLM(
        {CandidateJudgeResponse: [make_judge_response({"INC-124": 0.10})]}
    )
    output = find_similar_incidents(
        idempotency_fp(),
        top_k=3,
        memory=memory,
        storage=seeded_storage,
        llm=llm,
    )
    inc124 = next(m for m in output.matches if m.incident_id == "INC-124")
    assert inc124.match_type == "partial_match"
    assert inc124.difference_note
    # ...and a causally-different single candidate means no confident matches.
    assert all(m.match_type != "match" or m.total < MIN_CONFIDENT_SCORE for m in output.matches)


def test_cold_start_empty_bank(tmp_path):
    from failureloop.storage import Storage

    storage = Storage(str(tmp_path / "empty.db"))
    memory = FakeMemory()
    llm = FakeLLM()
    output = find_similar_incidents(
        new_incident_fp(), memory=memory, storage=storage, llm=llm
    )
    assert output.cold_start
    assert output.matches == []
    assert "empty" in output.reason.lower()
    # The judge LLM must not even be called on an empty bank.
    assert llm.json_calls == []
    storage.close()


def test_recall_returns_nothing_is_cold_start(seeded_storage):
    memory = FakeMemory()  # no scripted hits
    llm = FakeLLM()
    output = find_similar_incidents(
        new_incident_fp(), memory=memory, storage=seeded_storage, llm=llm
    )
    assert output.cold_start or output.reason  # empty result with a reason
    assert not output.matches


def test_judge_failure_falls_back_to_token_overlap(seeded_storage):
    """If the judge call fails entirely, matching degrades to token overlap
    and says so, instead of crashing or fabricating scores."""
    memory = FakeMemory()
    memory.pattern_hits = [
        {"text": "INC-104 ...", "metadata": {"incident_id": "INC-104"}, "score": 0.8},
    ]
    llm = FakeLLM()  # no scripted judge response -> raises -> fallback
    output = find_similar_incidents(
        new_incident_fp(), memory=memory, storage=seeded_storage, llm=llm
    )
    assert output.matches
    match = output.matches[0]
    assert "fallback" in match.judge_rationale.lower()
    # Same words ('retries', 'worker pool', 'time out') -> non-zero overlap.
    assert 0.0 < match.components["causal_chain_similarity"] <= 1.0
