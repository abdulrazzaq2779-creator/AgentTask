"""Synthetic historical incidents for the demo and evaluation.

Each incident is deliberately worded differently so that the system has to
match on the underlying failure pattern, not on error text. The four
"retry-amplification family" incidents (101/104/108/118) share one causal
chain: slow downstream -> aggressive retries -> retry amplification ->
worker exhaustion -> timeouts.
"""

from __future__ import annotations

from failureloop.fingerprint import AttemptedFix, FailureFingerprint

HISTORICAL_INCIDENTS: list[dict] = [
    {
        "incident_id": "INC-101",
        "raw_text": (
            "On 2026-05-18 the payments team noticed the billing service returning 504s to "
            "upstream callers. The card-processor vendor had slowed down earlier that morning "
            "after their own capacity incident. Our billing workers kept re-issuing the same "
            "card authorization calls because there was no ceiling on reattempts. Within "
            "twenty minutes every worker slot was occupied by a waiting request and new "
            "payments queued up. Customers saw payments stuck in 'processing'. We first tried "
            "scaling the Postgres cluster read replicas — that had no effect since the "
            "database was idle. Bouncing the service cleared the backlog only briefly. What "
            "finally worked was putting an explicit cap on reattempts and opening the circuit "
            "toward the card processor. Afterwards we documented the retry budget policy in "
            "the payments runbook."
        ),
        "fingerprint": FailureFingerprint(
            service="billing-service",
            environment="production",
            region="us-east1",
            symptom="Card authorizations stuck in processing; 504s to upstream callers",
            error_type="Gateway timeout",
            triggering_change="Card processor vendor degraded",
            recent_deployment="none",
            infrastructure_change="No infrastructure change",
            dependency="card-processor",
            affected_component="Billing worker slots",
            failure_chain=[
                "Card processor latency increased",
                "Billing service reissued authorization calls without a cap",
                "Retry amplification consumed every worker slot",
                "New payments queued behind exhausted workers",
            ],
            customer_impact="Customers saw payments stuck in processing",
            severity="SEV-1",
            root_cause="Retry amplification toward a degraded card processor exhausted workers",
            attempted_fixes=[
                AttemptedFix(action="Scaled Postgres read replicas", outcome="failed", notes="Database was not the bottleneck"),
                AttemptedFix(action="Bounced the billing service", outcome="partial", notes="Backlog cleared briefly, then refilled"),
                AttemptedFix(action="Capped reattempts and opened circuit toward card processor", outcome="success"),
            ],
            successful_fixes=["Capped reattempts and opened circuit toward card processor"],
            failed_fixes=["Scaled Postgres read replicas"],
            resolution_time_minutes=55,
            preventive_action="Enforce retry budgets toward external processors",
            final_outcome="Resolved",
        ),
    },
    {
        "incident_id": "INC-104",
        "raw_text": (
            "INC-104 (2026-08-12, asia-south1): shortly after deploying checkout-api v3.8.1 the "
            "on-call saw p99 latency jump from 900ms to 25s. The payment gateway provider had "
            "quietly increased its tail latency; our calls to it started timing out and the "
            "service automatically retried each timeout. Because retries were unbounded, the "
            "thread pool that serves checkout requests filled to 100% and requests began to "
            "time out in droves. Shoppers could not complete payments at all. Raising the "
            "database instance size did nothing — CPU and IO on the DB were nominal. A rolling "
            "restart bought ten minutes of relief before threads saturated again. The "
            "resolution was to add bounded retries with exponential backoff plus a circuit "
            "breaker in front of the gateway. Closed as resolved after 42 minutes."
        ),
        "fingerprint": FailureFingerprint(
            service="checkout-api",
            environment="production",
            region="asia-south1",
            symptom="Checkout requests timed out; p99 latency 900ms to 25s",
            error_type="Gateway timeout",
            triggering_change="Payment provider tail latency increased",
            recent_deployment="checkout-api v3.8.1",
            infrastructure_change="No infrastructure change",
            dependency="payment-gateway",
            affected_component="Checkout worker thread pool",
            failure_chain=[
                "Payment provider latency increased",
                "Application retried timed-out requests aggressively",
                "Retries consumed worker threads",
                "Thread pool exhausted; requests timed out",
            ],
            customer_impact="Customers could not complete payments",
            severity="SEV-1",
            root_cause="Retry amplification caused worker-thread exhaustion",
            attempted_fixes=[
                AttemptedFix(action="Increased database instance size", outcome="failed", notes="DB CPU and IO were nominal"),
                AttemptedFix(action="Rolling restart", outcome="partial", notes="Ten minutes of relief, then re-saturated"),
                AttemptedFix(action="Bounded retries with backoff and a circuit breaker", outcome="success"),
            ],
            successful_fixes=["Bounded retries with backoff and a circuit breaker"],
            failed_fixes=["Increased database instance size"],
            resolution_time_minutes=42,
            preventive_action="Configure bounded retries, circuit breaking, and downstream timeout limits",
            final_outcome="Resolved",
        ),
    },
    {
        "incident_id": "INC-108",
        "raw_text": (
            "Datadog paged us at 03:12 with 'executor queue saturation' on the order-web "
            "service. Nothing had been deployed in the past three days. Investigation showed "
            "the inventory-scraper dependency had slowed to a crawl after their nightly index "
            "rebuild; every call took 30+ seconds. order-web retries each slow call up to five "
            "times, so effective load on the worker pool grew fivefold. Threads queued up until "
            "the executor rejected work. Users saw spinning order confirmations. We tried "
            "adding Kafka partitions for the order pipeline (irrelevant — the queue depth on "
            "Kafka was fine). We also tried restarting, which only helped until the pool "
            "refilled. Cutting the retry count from five to two and adding a circuit breaker "
            "restored service immediately. Preventive work: make the scraper calls fail fast "
            "with a two-second budget."
        ),
        "fingerprint": FailureFingerprint(
            service="order-web",
            environment="production",
            region="us-west2",
            symptom="Order confirmations spin; executor rejects work",
            error_type="Thread pool exhausted",
            triggering_change="Dependency nightly index rebuild slowed calls",
            recent_deployment="none",
            infrastructure_change="No infrastructure change",
            dependency="inventory-scraper",
            affected_component="Worker pool",
            failure_chain=[
                "Inventory scraper latency increased",
                "Fivefold retry amplification",
                "Executor queue saturated",
                "Worker pool rejected new orders",
            ],
            customer_impact="Users saw spinning order confirmations",
            severity="SEV-2",
            root_cause="Retry amplification against a slow scraper exhausted the worker pool",
            attempted_fixes=[
                AttemptedFix(action="Added Kafka partitions to order pipeline", outcome="failed", notes="Kafka depth was fine"),
                AttemptedFix(action="Restarted order-web", outcome="partial"),
                AttemptedFix(action="Reduced retries to two and added circuit breaker", outcome="success"),
            ],
            successful_fixes=["Reduced retries to two and added circuit breaker"],
            failed_fixes=["Added Kafka partitions to order pipeline"],
            resolution_time_minutes=63,
            preventive_action="Fail fast on scraper calls with a 2s budget",
            final_outcome="Resolved",
        ),
    },
    {
        "incident_id": "INC-112",
        "raw_text": (
            "Product reported checkout conversion dropped 40% during the 2026-06-27 flash sale. "
            "Latency dashboards showed checkout-api responding slowly but never erroring — p50 "
            "was 4s, p99 18s. Root cause: the payment-gateway integration had no circuit "
            "breaker; during the sale the provider slowed and every checkout thread blocked on "
            "a 30-second socket read, serializing all traffic. There was no retry storm this "
            "time — thread utilization grew because each request simply waited longer. Scaling "
            "the database and adding more web pods produced no improvement while the provider "
            "was slow. Deploying a resilience patch that wrapped gateway calls in a circuit "
            "breaker with a five-second budget brought latency back to normal within minutes."
        ),
        "fingerprint": FailureFingerprint(
            service="checkout-api",
            environment="production",
            region="asia-south1",
            symptom="Checkout latency p50 4s / p99 18s with no errors; conversion down 40%",
            error_type="High latency",
            triggering_change="Flash sale traffic + provider slowdown",
            recent_deployment="none",
            infrastructure_change="No infrastructure change",
            dependency="payment-gateway",
            affected_component="Blocking gateway calls",
            failure_chain=[
                "Payment gateway slowed during flash sale",
                "Every checkout thread blocked on a 30s socket read",
                "Worker threads serialized behind slow calls",
                "Latency climbed while errors stayed zero",
            ],
            customer_impact="Checkout conversion dropped 40% during flash sale",
            severity="SEV-1",
            root_cause="Missing circuit breaker let slow gateway calls block the checkout thread pool",
            attempted_fixes=[
                AttemptedFix(action="Scaled database", outcome="failed", notes="DB was not the bottleneck"),
                AttemptedFix(action="Added web pods", outcome="failed", notes="Each pod blocked identically"),
                AttemptedFix(action="Circuit breaker with 5s budget around gateway calls", outcome="success"),
            ],
            successful_fixes=["Circuit breaker with 5s budget around gateway calls"],
            failed_fixes=["Scaled database", "Added web pods"],
            resolution_time_minutes=71,
            preventive_action="Circuit-break all external calls with strict time budgets",
            final_outcome="Resolved",
        ),
    },
    {
        "incident_id": "INC-118",
        "raw_text": (
            "Alert: db-prod-cluster CPU at 97% for 15 minutes. The natural assumption was a "
            "database problem and the first instinct was to scale it. The DBA noticed query "
            "volume had grown 6x, not query complexity. Origin: the pricing-service was stuck "
            "in a retry loop against the slow price-feed upstream; each failed call was "
            "retried immediately with no backoff, hammering the DB with lookups between "
            "attempts. Adding a read replica changed nothing. Killing the retry loop (adding "
            "backoff + jitter and a max attempt count) dropped DB CPU to 30% within two "
            "minutes. Documented in the reliability wiki as 'the retry storm that looked like "
            "a database incident'."
        ),
        "fingerprint": FailureFingerprint(
            service="pricing-service",
            environment="production",
            region="us-east1",
            symptom="DB CPU 97%; query volume 6x normal",
            error_type="Resource saturation",
            triggering_change="Upstream price feed slowed",
            recent_deployment="none",
            infrastructure_change="No infrastructure change",
            dependency="price-feed",
            affected_component="Database connection pool / retry loop",
            failure_chain=[
                "Price feed slowed",
                "Immediate no-backoff retries",
                "Retry storm multiplied DB lookups",
                "DB CPU saturated at 97%",
            ],
            customer_impact="Price lookups slow across the site",
            severity="SEV-2",
            root_cause="Retry storm (no backoff) against slow upstream, disguised as a database problem",
            attempted_fixes=[
                AttemptedFix(action="Added a read replica", outcome="failed", notes="Load was retry-induced, not read-induced"),
                AttemptedFix(action="Backoff + jitter + max attempts on the retry loop", outcome="success"),
            ],
            successful_fixes=["Backoff + jitter + max attempts on the retry loop"],
            failed_fixes=["Added a read replica"],
            resolution_time_minutes=25,
            preventive_action="Ban immediate no-backoff retries; require jitter and attempt caps",
            final_outcome="Resolved",
        ),
    },
    {
        "incident_id": "INC-124",
        "raw_text": (
            "Billing reconciliation flagged 214 double-charged subscriptions on 2026-06-03. "
            "Users clicking 'Pay' twice created two identical requests; the checkout handler "
            "had no idempotency guard, so both requests executed the charge. The payment-gateway "
            "itself was healthy throughout. Fix: generate a client-side idempotency key per "
            "checkout session and have the gateway deduplicate on it. Refunds were issued "
            "manually. A load-balancer restart suggested early on was unrelated and had no "
            "effect."
        ),
        "fingerprint": FailureFingerprint(
            service="checkout-api",
            environment="production",
            region="global",
            symptom="214 duplicate subscription charges",
            error_type="Data integrity",
            triggering_change="None — user double-submit",
            recent_deployment="none",
            infrastructure_change="No infrastructure change",
            dependency="payment-gateway",
            affected_component="Checkout handler",
            failure_chain=[
                "User double-clicked pay button",
                "Two identical charge requests accepted",
                "No idempotency key on checkout handler",
                "Gateway executed both charges",
            ],
            customer_impact="Customers double-charged; manual refunds required",
            severity="SEV-2",
            root_cause="Missing idempotency key on payment submission",
            attempted_fixes=[
                AttemptedFix(action="Restarted load balancer", outcome="failed", notes="Unrelated"),
                AttemptedFix(action="Client idempotency key + gateway dedup", outcome="success"),
            ],
            successful_fixes=["Client idempotency key + gateway dedup"],
            failed_fixes=["Restarted load balancer"],
            resolution_time_minutes=120,
            preventive_action="Require idempotency keys for all money-moving endpoints",
            final_outcome="Resolved",
        ),
    },
    {
        "incident_id": "INC-131",
        "raw_text": (
            "Ops noticed the fulfillment queue depth climbing linearly from 06:00 and alarms "
            "fired at 09:30 when latency-to-ship exceeded one hour. The consumer pod was "
            "processing fine individually, but every message that touched the flaky warehouse "
            "API was failing, being requeued, and failing again — an unbounded consumer retry "
            "loop starving throughput for healthy messages. Scaling consumers made the backlog "
            "grow faster, not shrink, since retries multiplied. The fix was a three-attempt cap "
            "with a dead-letter queue for failures, plus a timeout on the warehouse API call. "
            "Backlog drained in 40 minutes after deploy. Preventive action: all consumers must "
            "declare max retries and a DLQ."
        ),
        "fingerprint": FailureFingerprint(
            service="fulfillment-worker",
            environment="production",
            region="eu-west1",
            symptom="Fulfillment queue depth growing; ship latency > 1 hour",
            error_type="Queue backlog",
            triggering_change="Warehouse API flakiness",
            recent_deployment="none",
            infrastructure_change="No infrastructure change",
            dependency="warehouse-api",
            affected_component="Consumer retry loop",
            failure_chain=[
                "Warehouse API calls flaky",
                "Consumer requeued failures without limit",
                "Retry loop starved healthy-message throughput",
                "Backlog grew linearly",
            ],
            customer_impact="Orders shipped late",
            severity="SEV-2",
            root_cause="Unbounded consumer retry loop against a flaky dependency",
            attempted_fixes=[
                AttemptedFix(action="Scaled consumer pods", outcome="failed", notes="Amplified the retry storm"),
                AttemptedFix(action="Retry cap + dead-letter queue + warehouse API timeout", outcome="success"),
            ],
            successful_fixes=["Retry cap + dead-letter queue + warehouse API timeout"],
            failed_fixes=["Scaled consumer pods"],
            resolution_time_minutes=40,
            preventive_action="Every consumer declares max retries and a DLQ",
            final_outcome="Resolved",
        ),
    },
    {
        "incident_id": "INC-137",
        "raw_text": (
            "Marketing escalations: SMS/push notifications delayed up to 2 hours on "
            "2026-07-19. The notifier service consumed from the notifications topic, but its "
            "failure handling re-enqueued every rejected message at the head of the queue with "
            "no cap, creating a retry storm against the SMS provider which was throttling us. "
            "Throughput collapsed. The team first tried bumping topic partitions and restarting "
            "brokers — no change, the problem was consumer behavior. Enforcing a maximum of two "
            "attempts, a 5-minute backoff, and routing exhausted messages to the dead-letter "
            "queue restored normal delivery in under 15 minutes. Preventive action: DLQ "
            "monitoring alerts at backlog > 1000."
        ),
        "fingerprint": FailureFingerprint(
            service="notifier-service",
            environment="production",
            region="global",
            symptom="Push/SMS notifications delayed up to 2 hours",
            error_type="Queue backlog",
            triggering_change="SMS provider throttling",
            recent_deployment="none",
            infrastructure_change="No infrastructure change",
            dependency="sms-provider",
            affected_component="Consumer requeue logic",
            failure_chain=[
                "SMS provider throttled requests",
                "Rejected messages re-enqueued without cap",
                "Retry storm consumed consumer throughput",
                "Notification delivery delayed hours",
            ],
            customer_impact="Customers received notifications late",
            severity="SEV-3",
            root_cause="Dead-letter-less retry storm against a throttling SMS provider",
            attempted_fixes=[
                AttemptedFix(action="Bumped topic partitions and restarted brokers", outcome="failed", notes="Broker-side, unrelated"),
                AttemptedFix(action="Max 2 attempts + 5min backoff + DLQ routing", outcome="success"),
            ],
            successful_fixes=["Max 2 attempts + 5min backoff + DLQ routing"],
            failed_fixes=["Bumped topic partitions and restarted brokers"],
            resolution_time_minutes=15,
            preventive_action="DLQ backlog alerts; cap all provider retries",
            final_outcome="Resolved",
        ),
    },
]

# Assign incident IDs onto the fingerprint objects at import time so the
# dataset is self-consistent (get_historical_fingerprints returns deep copies).
for _incident in HISTORICAL_INCIDENTS:
    _incident["fingerprint"].incident_id = _incident["incident_id"]

# Raw-text-only new incidents used to test extraction + matching.
TEST_INCIDENTS: list[dict] = [
    {
        "incident_id": "INC-201",
        "raw_text": (
            "On-call: checkout requests are timing out since roughly 14:00 UTC. The latest "
            "checkout-api deploy went out an hour ago. The payment provider is showing elevated "
            "latency on their status page. Worker utilization on our side is pegged at 100% and "
            "customers report being unable to finish purchases."
        ),
        "expect_pattern": "retry-amplification",
        "expect_match_ids": ["INC-104", "INC-101", "INC-112"],
    },
    {
        "incident_id": "INC-202",
        "raw_text": (
            "Finance reports a small number of customers were charged twice for the same order "
            "this weekend. The gateway shows two successful captures per affected order and our "
            "logs show the checkout endpoint processed both requests normally."
        ),
        "expect_pattern": "missing-idempotency",
        "expect_match_ids": ["INC-124"],
    },
    {
        "incident_id": "INC-203",
        "raw_text": (
            "The email dispatch queue has been backing up for two hours. The upstream email "
            "vendor is rate-limiting us, and our worker keeps requeueing failed sends "
            "indefinitely, so nothing is making progress."
        ),
        "expect_pattern": "dlq-retry-storm",
        "expect_match_ids": ["INC-137", "INC-131"],
    },
    {
        "incident_id": "INC-204",
        "raw_text": (
            "Search is throwing intermittent 500s after the search-api v9.2 rollout. Logs show "
            "an NPE inside the new ranking code path when a query has no facets."
        ),
        "expect_pattern": "new-pattern",
        "expect_match_ids": [],
    },
]

PATTERN_LABELS: dict[str, str] = {
    "retry-amplification": "Downstream latency -> aggressive retries -> worker/thread exhaustion",
    "missing-idempotency": "Duplicate submissions accepted without idempotency keys",
    "dlq-retry-storm": "Provider throttling -> unbounded consumer requeue -> backlog",
    "missing-circuit-breaker": "Slow external calls block worker threads with no breaker",
    "new-pattern": "No historical precedent",
}


def get_historical_fingerprints() -> list[FailureFingerprint]:
    fingerprints: list[FailureFingerprint] = []
    for incident in HISTORICAL_INCIDENTS:
        fp = incident["fingerprint"].model_copy(deep=True)
        fp.incident_id = incident["incident_id"]
        fingerprints.append(fp)
    return fingerprints


def get_raw_incident_texts() -> dict[str, str]:
    return {incident["incident_id"]: incident["raw_text"] for incident in HISTORICAL_INCIDENTS}


# Hand-written fingerprints representing each known failure-pattern family.
# Used by the LLM-free evaluation harness to test the matcher directly.
PATTERN_SEED_FINGERPRINTS: dict[str, FailureFingerprint] = {
    "retry-amplification": FailureFingerprint(
        service="checkout-api",
        symptom="Requests timing out, worker utilization 100%",
        error_type="Gateway timeout",
        dependency="payment-gateway",
        triggering_change="Payment provider latency increased",
        failure_chain=[
            "Downstream dependency latency increased",
            "Application retried requests aggressively",
            "Retry amplification consumed worker threads",
            "Worker pool exhausted; requests timed out",
        ],
    ),
    "missing-idempotency": FailureFingerprint(
        service="checkout-api",
        symptom="Duplicate charges for the same order",
        error_type="Data integrity",
        dependency="payment-gateway",
        triggering_change="User double-submit",
        failure_chain=[
            "Duplicate request accepted",
            "No idempotency key",
            "Charge executed twice",
        ],
    ),
    "dlq-retry-storm": FailureFingerprint(
        service="notifier-service",
        symptom="Queue backlog; deliveries delayed",
        error_type="Queue backlog",
        dependency="sms-provider",
        triggering_change="Provider throttling",
        failure_chain=[
            "Provider throttled requests",
            "Failed messages requeued without limit",
            "Retry storm consumed throughput",
            "Backlog grew",
        ],
    ),
    "missing-circuit-breaker": FailureFingerprint(
        service="checkout-api",
        symptom="High latency with zero errors",
        error_type="High latency",
        dependency="payment-gateway",
        triggering_change="Traffic spike with provider slowdown",
        failure_chain=[
            "External provider slowed",
            "Calls blocked with long timeouts and no breaker",
            "Threads serialized",
            "Latency climbed",
        ],
    ),
}
