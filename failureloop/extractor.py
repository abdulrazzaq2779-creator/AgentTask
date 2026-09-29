"""LLM-based fingerprint extraction from raw incident text.

Uses OpenAI structured outputs so the model must return a valid
FailureFingerprint JSON document.
"""

from __future__ import annotations

import json

from openai import OpenAI

from failureloop.config import Config
from failureloop.fingerprint import FailureFingerprint

SYSTEM_PROMPT = """You are an SRE incident analyst. From raw incident input
(alert text, logs, engineer description, postmortem), extract a structured
failure fingerprint as JSON.

Rules:
- Extract only facts present in the text; use "" or empty lists when a field is unknown.
- failure_chain must be an ordered causal chain from trigger to observed impact.
- attempted_fixes: every remedy mentioned; set outcome to "success", "failed",
  "partial", or "unknown" based on what the text says happened.
- successful_fixes / failed_fixes must be consistent with attempted_fixes outcomes.
- Normalise names (e.g. "payment gateway" and "payment-gateway" are the same).
- severity: SEV-1..SEV-4 if stated or clearly implied, otherwise "".
"""


SERVICE_KEYWORDS = {
    "checkout-api": ["checkout"],
    "billing-service": ["billing"],
    "order-web": ["order"],
    "notifier-service": ["notifier", "notification", "email dispatch", "sms", "push"],
    "fulfillment-worker": ["fulfillment"],
    "pricing-service": ["pricing"],
    "search-api": ["search"],
}

DEPENDENCY_KEYWORDS = {
    "payment-gateway": ["payment gateway", "payment provider", "payments provider"],
    "card-processor": ["card processor", "card-processor"],
    "sms-provider": ["sms provider", "email vendor", "email provider"],
    "warehouse-api": ["warehouse"],
    "price-feed": ["price feed", "price-feed"],
    "inventory-scraper": ["inventory scraper", "inventory-scraper"],
}

RETRY_WORDS = ["retry", "retries", "retrying", "retried", "reattempt", "requeue", "re-enqueue", "requeueing"]
EXHAUSTION_WORDS = ["worker", "thread", "executor", "pool", "utilization", "utilization 100", "100%"]
TIMEOUT_WORDS = ["timeout", "timed out", "time out", "timeouts"]
DUPLICATE_WORDS = ["duplicate", "charged twice", "double-charged", "charged twice"]
BACKLOG_WORDS = ["backlog", "queue depth", "backing up", "backing-up"]


def heuristic_extract(raw_text: str, incident_id: str = "") -> FailureFingerprint:
    """Keyword-based extraction used when no LLM key is configured."""
    text = raw_text.lower()

    service = ""
    for candidate, keywords in SERVICE_KEYWORDS.items():
        if any(k in text for k in keywords):
            service = candidate
            break

    dependency = ""
    for candidate, keywords in DEPENDENCY_KEYWORDS.items():
        if any(k in text for k in keywords):
            dependency = candidate
            break

    has_retry = any(w in text for w in RETRY_WORDS)
    has_exhaustion = any(w in text for w in EXHAUSTION_WORDS)
    has_timeout = any(w in text for w in TIMEOUT_WORDS)
    has_duplicate = any(w in text for w in DUPLICATE_WORDS)
    has_backlog = any(w in text for w in BACKLOG_WORDS)
    # Worker exhaustion plus downstream timeouts implies a retry storm even
    # when the word "retry" is absent from the alert text.
    retry_family = has_retry or (has_exhaustion and (has_timeout or has_backlog))

    chain: list[str] = []
    if has_duplicate:
        root_cause = "Missing idempotency key on payment submission"
        chain = ["Duplicate request accepted", "No idempotency key", "Charge executed twice"]
    elif retry_family and has_backlog and not has_exhaustion:
        root_cause = "Unbounded consumer retry loop against a throttling provider"
        chain = [
            "Provider throttled requests",
            "Failed messages requeued without limit",
            "Retry storm consumed throughput",
            "Backlog grew",
        ]
    elif retry_family:
        root_cause = "Retry amplification toward a slow dependency exhausted workers"
        chain = [
            "Downstream dependency latency increased",
            "Application retried requests aggressively",
            "Retry amplification consumed worker threads",
            "Worker pool exhausted; requests timed out",
        ]
    else:
        root_cause = ""

    first_sentence = raw_text.strip().split(".")[0][:200]
    return FailureFingerprint(
        incident_id=incident_id,
        service=service,
        dependency=dependency,
        symptom=first_sentence,
        error_type=("Gateway timeout" if has_timeout else "Queue backlog" if has_backlog else ""),
        failure_chain=chain,
        root_cause=root_cause,
    )


class FingerprintExtractor:
    def __init__(self, config: Config):
        self.config = config
        self.client = OpenAI(api_key=config.openai_api_key) if config.has_openai else None

    def extract(self, raw_text: str, incident_id: str = "") -> FailureFingerprint:
        if self.client is None:
            # Graceful degradation: heuristic extraction keeps the pipeline
            # (and demo) running without an LLM key.
            return heuristic_extract(raw_text, incident_id=incident_id)

        response = self.client.chat.completions.create(
            model=self.config.openai_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": raw_text},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "failure_fingerprint",
                    "schema": FailureFingerprint.model_json_schema(),
                    "strict": False,
                },
            },
            temperature=0.1,
        )

        payload = json.loads(response.choices[0].message.content or "{}")
        fingerprint = FailureFingerprint.model_validate(payload)
        fingerprint.incident_id = incident_id or fingerprint.incident_id
        return fingerprint
