"""Fix ranker tests: separation, ranking order, conflicting evidence."""

from __future__ import annotations

from failureloop.matching.fix_ranker import rank_fixes
from failureloop.matching.matcher import MatchResult
from tests.conftest import fingerprint_by_id


def make_match(incident_id: str, total: float = 0.7, recency: float = 0.8) -> MatchResult:
    return MatchResult(
        incident_id=incident_id,
        total=total,
        components={
            "causal_chain_similarity": total,
            "dependency_match": 0.8,
            "trigger_match": 0.5,
            "recency": recency,
            "severity_relevance": 0.7,
        },
        judge_rationale="test",
        fingerprint=fingerprint_by_id(incident_id),
    )


def test_successful_and_failed_separated():
    ranked = rank_fixes([make_match("INC-104")])
    actions = [fix.action.lower() for fix in ranked.proven_fixes]
    failed = [fix.action.lower() for fix in ranked.failed_fixes]
    assert any("bounded retries" in a for a in actions)
    assert any("database instance" in f for f in failed)
    # Every fix carries its supporting incident.
    for fix in ranked.proven_fixes + ranked.failed_fixes:
        assert fix.supporting_incident_ids == ["INC-104"]


def test_fix_working_twice_ranks_higher():
    """The same fix in two incidents aggregates to count 2 and outranks singles."""
    m1, m2 = make_match("INC-104", total=0.6), make_match("INC-112", total=0.8)
    m1.fingerprint.successful_fixes = ["Bounded retries with a circuit breaker"]
    m2.fingerprint.successful_fixes = ["Bounded retries with a circuit breaker"]
    ranked = rank_fixes([m1, m2])
    top = ranked.proven_fixes[0]
    assert top.incident_count == 2
    assert set(top.supporting_incident_ids) == {"INC-104", "INC-112"}


def test_conflicting_evidence_preserved():
    """A fix that worked in one incident and failed in another shows both."""
    match_success = make_match("INC-112")
    match_success.fingerprint.successful_fixes = ["Scaled database capacity"]
    match_success.fingerprint.failed_fixes = []  # keep the scenario unambiguous

    match_failure = make_match("INC-104")
    match_failure.fingerprint.failed_fixes = ["Scaled database capacity"]
    match_failure.fingerprint.successful_fixes = ["Bounded retries with a circuit breaker"]

    ranked = rank_fixes([match_success, match_failure])
    assert len(ranked.conflicting_fixes) == 1
    conflict = ranked.conflicting_fixes[0]
    assert "database" in conflict.action.lower()
    assert conflict.worked_in == ["INC-112"]
    assert conflict.failed_in == ["INC-104"]
    # The conflicted fix leaves the clean lists; unrelated fixes remain.
    assert all(f.action.lower() != conflict.action.lower() for f in ranked.proven_fixes)
    assert all(f.action.lower() != conflict.action.lower() for f in ranked.failed_fixes)
    assert any("circuit breaker" in f.action.lower() for f in ranked.proven_fixes)


def test_partial_matches_excluded():
    match = make_match("INC-104")
    match.match_type = "partial_match"
    ranked = rank_fixes([match])
    assert not ranked.has_evidence


def test_variant_grouping():
    """Same fix with different wording counts once with variants kept."""
    m1, m2 = make_match("INC-104"), make_match("INC-118")
    m1.fingerprint.successful_fixes = ["Capped retries and added a circuit breaker"]
    m2.fingerprint.successful_fixes = ["Capped retries + circuit breaker"]
    ranked = rank_fixes([m1, m2])
    matching = [f for f in ranked.proven_fixes if "capped retries" in f.action.lower()]
    assert len(matching) == 1
    assert matching[0].incident_count == 2
    assert len(matching[0].variants) == 2
