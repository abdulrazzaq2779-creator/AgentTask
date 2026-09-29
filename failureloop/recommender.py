"""Evidence-based recommendation generation (RAG pattern).

Two modes:
- stateless: LLM sees only the current incident (generic advice).
- memory: LLM sees the current incident plus matched historical incidents,
  proven fixes, and failed-fix warnings.
A rule-based generator is used when no LLM is configured or the LLM fails.
"""

from __future__ import annotations

import json

from openai import OpenAI

from failureloop.fingerprint import FailureFingerprint, FingerprintMatch, Recommendation
from failureloop.matcher import similar_fix_counts

SYSTEM_PROMPT = """You are FailureLoop, an incident-response assistant with
memory of past incidents. You will receive the current incident's fingerprint
plus matched historical incidents with their outcomes.

Rules:
- Ground every claim in the provided historical incidents; cite incident IDs.
- Clearly separate confirmed evidence ("Historical evidence: ...") from
  hypotheses ("Hypothesis: ...").
- Rank fixes by historical success; do NOT recommend anything that failed in a
  comparable incident without explaining the risk.
- If matches are weak or absent, say this may be a new failure pattern and give
  safe generic guidance.
- Keep it practical for an on-call engineer under pressure.
"""


def _fingerprint_block(fp: FailureFingerprint, include_fixes: bool = True) -> str:
    lines = [f"Incident {fp.incident_id}:"]
    fields = {
        "service": fp.service,
        "symptom": fp.symptom,
        "error_type": fp.error_type,
        "triggering_change": fp.triggering_change,
        "dependency": fp.dependency,
        "root_cause": fp.root_cause,
        "customer_impact": fp.customer_impact,
        "severity": fp.severity,
    }
    for key, value in fields.items():
        if value:
            lines.append(f"  {key}: {value}")
    if fp.failure_chain:
        lines.append("  failure_chain: " + " -> ".join(fp.failure_chain))
    if include_fixes:
        if fp.successful_fixes:
            lines.append("  fixes that WORKED: " + "; ".join(fp.successful_fixes))
        if fp.failed_fixes:
            lines.append("  fixes that FAILED: " + "  ".join(fp.failed_fixes))
    return "\n".join(lines)


def _memory_context_block(
    new_fp: FailureFingerprint,
    matches: list[FingerprintMatch],
    success_counts: dict[str, int],
    failed_counts: dict[str, int],
) -> str:
    lines = ["Matched historical incidents (ranked by pattern similarity):"]
    for match in matches:
        lines.append("")
        lines.append(_fingerprint_block(match.stored_fingerprint))
        lines.append(f"  match_score: {match.score}; reasons: {', '.join(match.reasons) or 'n/a'}")
    lines.append("")
    lines.append("Aggregated fix outcomes across the matches above:")
    lines.append("Worked multiple times: " + json.dumps(success_counts or {}))
    lines.append("Failed before: " + json.dumps(failed_counts or {}))
    return "\n".join(lines)


def _rule_based_recommendation(
    new_fp: FailureFingerprint,
    matches: list[FingerprintMatch],
    success_counts: dict[str, int],
    failed_counts: dict[str, int],
) -> Recommendation:
    if not matches:
        return Recommendation(
            summary="No strong historical match was found; this may be a new failure pattern.",
            likely_root_cause=(
                f"Unknown. Hypothesis: investigate dependency latency, recent deployments, "
                f"and retry configuration around {new_fp.service or 'the affected service'}."
            ),
            investigation_steps=[
                "Check recent deployments and config changes for the affected service.",
                "Verify downstream dependency latency and error rates.",
                "Inspect retry/timeout configuration and thread-pool utilization.",
                "Capture the confirmed root cause when resolved so memory improves.",
            ],
            proven_fixes=[],
            failed_fix_warnings=[],
            prevention="Record the root cause and prevention after resolution.",
            confidence="low",
            evidence=[],
        )

    steps: list[str] = []
    if new_fp.dependency:
        steps.append(f"Check latency and error rates on dependency '{new_fp.dependency}'.")
    steps.append("Inspect retry counts and retry configuration (attempts, backoff, budget).")
    steps.append("Check thread-pool / worker utilization for exhaustion patterns.")
    steps.append("Verify circuit-breaker state and downstream timeout budgets.")

    proven: list[str] = [
        f"{fix} (worked in {count} matched incident(s))"
        for fix, count in sorted(success_counts.items(), key=lambda kv: -kv[1])
    ]
    warnings = [
        f"'{fix}' was attempted and failed in {count} matched incident(s); avoid as a first move."
        for fix, count in sorted(failed_counts.items(), key=lambda kv: -kv[1])
    ]

    top = matches[0]
    return Recommendation(
        summary=(
            f"Closest historical pattern: {top.stored_fingerprint.root_cause or top.stored_fingerprint.symptom} "
            f"(match score {top.score:.2f} with {top.incident_id})."
        ),
        likely_root_cause=top.stored_fingerprint.root_cause,
        investigation_steps=steps,
        proven_fixes=proven,
        failed_fix_warnings=warnings,
        prevention=top.stored_fingerprint.preventive_action,
        confidence="high" if (len(matches) >= 2 and top.score >= 0.5) else "medium",
        evidence=[
            f"{m.incident_id} (score {m.score:.2f}): {', '.join(m.reasons) or 'pattern overlap'}"
            for m in matches[:3]
        ],
    )


class Recommender:
    def __init__(self, config):
        self.config = config
        self.client = OpenAI(api_key=config.openai_api_key) if config.has_openai else None

    def generate(
        self,
        new_fp: FailureFingerprint,
        matches: list[FingerprintMatch],
        mode: str = "memory",
    ) -> Recommendation:
        success_counts, failed_counts = similar_fix_counts(matches)

        if self.client is None or mode == "rule-based":
            return _rule_based_recommendation(new_fp, matches, success_counts, failed_counts)

        context = ""
        if matches:
            context = _memory_context_block(new_fp, matches, success_counts, failed_counts)
        elif mode == "memory":
            context = "No historical matches found; treat this as a possibly new failure pattern."

        user_prompt = (
            "Current incident fingerprint:\n"
            + _fingerprint_block(new_fp, include_fixes=False)
            + "\n\n"
            + context
            + "\n\nProduce: summary, likely_root_cause (mark hypotheses), "
            "investigation_steps (ordered), proven_fixes, failed_fix_warnings, "
            "prevention, confidence (low/medium/high), evidence (incident IDs)."
        )

        try:
            response = self.client.chat.completions.create(
                model=self.config.openai_model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "recommendation",
                        "schema": Recommendation.model_json_schema(),
                        "strict": False,
                    },
                },
                temperature=0.2,
            )
            payload = json.loads(response.choices[0].message.content or "{}")
            rec = Recommendation.model_validate(payload)
            return rec
        except Exception as exc:
            print(f"[FailureLoop] LLM recommendation failed ({exc}); using rule-based output.")
            return _rule_based_recommendation(new_fp, matches, success_counts, failed_counts)
