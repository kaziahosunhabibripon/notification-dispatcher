# Repository Instructions

## Product scope

This repository is a production-style scheduled-notification dispatcher for
campaigns of up to 1,000,000 recipients. It must support multiple tenants and
campaigns, scheduled dispatch, independent workers, retries, cancellation,
crash recovery, accurate statistics, observability, and measurable load tests.
Build production code, not tutorial examples or pseudocode.

## Required stack

- Python 3.12+, FastAPI, Pydantic
- PostgreSQL, SQLAlchemy 2.x async, asyncpg
- Redis for distributed rate limiting
- HTTPX, Docker Compose, pytest/pytest-asyncio, Ruff

PostgreSQL is the durable work queue and authoritative source of campaign and
message state. Do not introduce Kafka, Kubernetes, or other brokers/services
unless measurements justify the change.

## Non-negotiable design rules

1. Do not load all recipients into application memory; use streaming/chunked
   imports and bounded batches.
2. Never begin a send before campaign.send_at.
3. Coordinate claims with PostgreSQL transactions and FOR UPDATE SKIP LOCKED.
4. Commit a claim before provider I/O; never hold a database transaction open
   while making network requests.
5. Use expiring leases and fencing tokens. Every worker state update must be
   conditional on the current lease token.
6. Use the stable provider idempotency key
   notification:{campaign_id}:{user_id} for every retry.
7. Treat provider acceptance separately from recipient delivery.
8. Use Redis-backed shared, atomic rate limits; never use process-local
   counters for distributed quotas.
9. Make campaign creation and cancellation idempotent where appropriate.
10. Measure before scaling workers, pools, partitions, read replicas, or
    infrastructure.

## Implementation phases

Work in this order unless the user explicitly requests a different scope.

### Phase 1 — Foundation

Create repository structure, environment configuration, Docker Compose,
PostgreSQL schema, async SQLAlchemy session management, /health, /health/db,
basic tests, and exact setup documentation.

Schema must include campaigns, campaign_recipients, and messages with keys,
foreign keys, uniqueness, timestamps, checked statuses, and appropriate
indexes.

### Phase 2 — Campaign API and bulk import

Implement campaign create/read endpoints, recipient validation, atomic
campaign creation, chunked inserts, create idempotency, exact counts, and
pagination. A failed creation must leave no partially initialized campaign.

### Phase 3 — Mock provider

Implement a separate provider with /send and /stats, durable idempotency
storage, acceptance timestamps, configurable transient failures, and
configurable timeout-after-acceptance behavior.

### Phase 4 — Single worker

Implement a separate worker entry point, eligible-message selection, bounded
transactional claims, scheduled dispatch, consistent state transitions, HTTP
provider client, and graceful shutdown.

### Phase 5 — Retries and idempotency

Implement exponential backoff with jitter, Retry-After, a maximum of five
attempts, transient/permanent classification, unknown-outcome handling, and
structured error records. Never infer that a timeout means rejection.

### Phase 6 — Multi-worker recovery

Support independent workers, SKIP LOCKED, lease expiration, fencing tokens,
conditional state updates, stale-worker protection, and abandoned-message
recovery.

### Phase 7 — Cancellation and rate limiting

Implement idempotent cancellation with documented linearization semantics,
Redis atomic tenant/provider quotas, bounded concurrency, and fair scheduling.
After cancellation commits, no worker may begin a new request for that
campaign. Already in-flight requests retain their actual provider outcomes.

### Phase 8 — Observability and operations

Provide structured logs, correlation IDs, queue depth, oldest-message age,
accepted sends/sec, retry rate, provider latency, DB pool/saturation metrics,
error alerts, tested backup/restore instructions, and a data-retention policy.

### Phase 9 — Integration and load testing

Test one API, two separate workers, one mock provider, shared PostgreSQL, and
shared Redis. Progressively test 1k, 10k, 100k, and 1m recipients. Verify
single provider acceptance per logical message, scheduling, retry/timeout
correctness, recovery, cancellation, accurate counts, quota behavior,
throughput, p95 latency, queue age, and resource use.

Never say a million-message test passed unless it actually completed.

## Required workflow for every phase

1. Inspect the existing implementation.
2. Implement only the active phase.
3. Run relevant tests and lint/format checks.
4. Fix failures.
5. Inspect database state and service logs where the environment is available.
6. Review the code changes and preserve unrelated files.
7. Update README and relevant operational/design documentation.
8. Report the exact commands run and their actual results.
9. Stop after the phase and wait for verification before the next phase.

Do not skip tests, silently ignore failures, or claim unrun tests passed.
Clearly report unavailable external dependencies (for example, a stopped Docker
daemon) and mark the affected integration checks as not run/skipped.

## Performance and change discipline

Benchmark insert chunk size, claim batch size, worker concurrency, DB pool
size, provider latency, and rate-limit contention. Consider PgBouncer only
when connection measurements warrant it. Consider a queue/broker, partitioning,
reporting counters, or replicas only after measurements and query plans justify
them. Do not scale worker count blindly.

## Required deliverables

- Working source code and hand-written schema.sql
- Docker Compose setup and .env.example
- Unit, integration, and load-test code plus honest run results
- README with exact Ubuntu/local commands
- Architecture, state-transition, failure/recovery, backup/restore, retention,
  performance, limitations, and risk documentation
