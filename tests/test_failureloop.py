"""Tests for matcher, storage, and agent (no LLM required)."""

from __future__ import annotations

import os
import tempfile

import pytest

from failureloop.data import (
    HISTORICAL_INCIDENTS,
    TEST_INCIDENTS,
    get_historical_fingerprints,
)
from failureloop.fingerprint import FailureFingerprint
from failureloop.matcher import match_fingerprints, similar_fix_counts
from failureloop.storage import Storage


@pytest.fixture()
def tmp_storage():
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(os.path.join(tmp, "test.db"))
        yield storage
        storage.close()


def test_historical_dataset_complete():
    assert len(HISTORICAL_INCIDENTS) == 8
    ids = [i["fingerprint"].incident_id for i in HISTORICAL_INCIDENTS]
    assert len(set(ids)) == 8


def test_retry_amplification_family_matches():
    """INC-202-style incidents (timeout + retries + worker exhaustion) should
    match the retry-amplification family despite different wording."""
    fingerprints = get_historical_fingerprints()
    new_fp = FailureFingerprint(
        service="checkout-api",
        symptom="Checkout requests timing out",
        error_type="Gateway timeout",
        dependency="payment-gateway",
        failure_chain=[
            "Payment provider latency increased",
            "Application retried aggressively",
            "Worker pool exhausted",
            "Requests timed out",
        ],
    )
    matches = match_fingerprints(new_fp, fingerprints)
    assert matches, "Expected at least one match"
    top_ids = {m.incident_id for m in matches[:3]}
    assert top_ids & {"INC-101", "INC-104", "INC-108", "INC-118"}, (
        f"Expected retry-amplification family in top matches, got {top_ids}"
    )


def test_idempotency_pattern_matches():
    fingerprints = get_historical_fingerprints()
    new_fp = FailureFingerprint(
        service="checkout-api",
        symptom="Duplicate charges",
        root_cause="Missing idempotency key",
        dependency="payment-gateway",
        failure_chain=[
            "Double submit",
            "Two identical charges accepted",
            "No idempotency guard",
        ],
    )
    matches = match_fingerprints(new_fp, fingerprints)
    assert matches[0].incident_id == "INC-124"


def test_no_match_for_new_pattern():
    fingerprints = get_historical_fingerprints()
    new_fp = FailureFingerprint(
        service="search-api",
        symptom="Intermittent 500s",
        error_type="Null pointer exception",
        failure_chain=["New ranking code path", "NPE on empty facets"],
    )
    matches = match_fingerprints(new_fp, fingerprints, min_score=0.15)
    assert all(m.score < 0.5 for m in matches)


def test_outcome_boost_improves_ranking(tmp_storage):
    fingerprints = get_historical_fingerprints()
    new_fp = FailureFingerprint(
        service="checkout-api",
        symptom="Checkout timeouts",
        dependency="payment-gateway",
        failure_chain=["Gateway latency", "Retries", "Worker exhaustion"],
    )
    base = match_fingerprints(new_fp, fingerprints)
    boosted = match_fingerprints(new_fp, fingerprints, fix_outcomes={"INC-104": 1.0})
    base_top = next(m for m in base if m.incident_id == "INC-104")
    boosted_top = next(m for m in boosted if m.incident_id == "INC-104")
    assert boosted_top.score > base_top.score


def test_storage_roundtrip(tmp_storage):
    fp = HISTORICAL_INCIDENTS[0]["fingerprint"].model_copy(deep=True)
    tmp_storage.save_incident(fp, "raw text")
    loaded = tmp_storage.load_incident(fp.incident_id)
    assert loaded is not None
    loaded_fp, raw = loaded
    assert loaded_fp.incident_id == fp.incident_id
    assert raw == "raw text"
    assert tmp_storage.incident_count() == 1


def test_feedback_quality(tmp_storage):
    fp = HISTORICAL_INCIDENTS[0]["fingerprint"].model_copy(deep=True)
    tmp_storage.save_incident(fp, "raw")
    tmp_storage.save_feedback(fp.incident_id, None, "worked", "confirmed")
    quality = tmp_storage.feedback_outcome_quality()
    assert quality[fp.incident_id] > 0.5


def test_fix_counts_aggregation():
    fingerprints = get_historical_fingerprints()
    new_fp = FailureFingerprint(
        service="checkout-api",
        dependency="payment-gateway",
        failure_chain=["Gateway latency", "Retries", "Worker exhaustion"],
    )
    matches = match_fingerprints(new_fp, fingerprints, min_score=0.0)
    success, failed = similar_fix_counts(matches)
    assert any("circuit" in fix for fix in success)
    assert any("database" in fix or "replica" in fix for fix in failed)
