"""Evaluation harness (no LLM needed): checks that the matcher retrieves the
correct historical pattern and ranks proven fixes for the test incidents.

Run:  python -m failureloop.evaluate
"""

from __future__ import annotations

from dataclasses import dataclass

from failureloop.data import TEST_INCIDENTS, get_historical_fingerprints
from failureloop.matcher import match_fingerprints, similar_fix_counts


@dataclass
class EvalRow:
    incident_id: str
    expected_pattern: str
    retrieved_correct: bool
    top_ids: list[str]
    top_score: float
    proven_fix_count: int
    failed_fix_count: int


def run_evaluation(min_score: float = 0.15) -> list[EvalRow]:
    fingerprints = get_historical_fingerprints()
    rows: list[EvalRow] = []

    for test in TEST_INCIDENTS:
        expected_ids = set(test["expect_match_ids"])
        # Build a fingerprint directly from the known pattern family for
        # LLM-free evaluation of the matcher itself.
        from failureloop.data import PATTERN_SEED_FINGERPRINTS

        seed = PATTERN_SEED_FINGERPRINTS.get(test["expect_pattern"])
        if seed is None:
            continue
        matches = match_fingerprints(seed, fingerprints, min_score=min_score)
        top_ids = [m.incident_id for m in matches[:3]]
        retrieved = bool(expected_ids & set(top_ids))
        success, failed = similar_fix_counts(matches)
        rows.append(
            EvalRow(
                incident_id=test["incident_id"],
                expected_pattern=test["expect_pattern"],
                retrieved_correct=retrieved,
                top_ids=top_ids,
                top_score=matches[0].score if matches else 0.0,
                proven_fix_count=len(success),
                failed_fix_count=len(failed),
            )
        )
    return rows


def main() -> None:
    rows = run_evaluation()
    print(f"{'incident':<10} {'pattern':<22} {'ok':<4} {'top matches':<28} {'score'}")
    for row in rows:
        ok = "OK" if row.retrieved_correct else "MISS"
        print(
            f"{row.incident_id:<10} {row.expected_pattern:<22} {ok:<4} "
            f"{','.join(row.top_ids) or '-':<28} {row.top_score:.2f}"
        )
    accuracy = sum(r.retrieved_correct for r in rows) / max(len(rows), 1)
    print(f"\nPattern retrieval accuracy: {accuracy:.0%} ({sum(r.retrieved_correct for r in rows)}/{len(rows)})")


if __name__ == "__main__":
    main()
