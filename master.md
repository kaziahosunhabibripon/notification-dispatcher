# MASTER PROJECT PLAN

# Production-Style Scheduled Notification Dispatcher

## Project Goal

Build a reliable Python backend that can schedule and dispatch notifications to campaigns containing up to 1,000,000 recipients.

The backend must support:

- Multiple tenants
- Multiple campaigns
- Scheduled delivery
- Multiple worker processes
- Distributed rate limiting
- Retries and provider timeouts
- Worker crash recovery
- Cancellation
- Accurate campaign statistics
- Monitoring and operational recovery
- Load and integration testing

This is a real implementation project, not a tutorial-only project.

## Technology Stack

- Python 3.12+
- FastAPI
- Pydantic
- PostgreSQL
- SQLAlchemy 2.x async
- asyncpg
- Redis for distributed rate limiting
- HTTPX
- Docker Compose
- pytest
- pytest-asyncio
- Ruff

Start with PostgreSQL as the durable work queue. Do not add Kafka, Kubernetes, or unnecessary microservices before measurements justify them.

## Architecture Principles

1. PostgreSQL is the authoritative source of campaign and message state.
2. Never load all one million recipients into application memory.
3. Use chunked imports and bounded batches.
4. Never send before campaign.send_at.
5. Use PostgreSQL transactions and FOR UPDATE SKIP LOCKED to coordinate workers.
6. Never hold database transactions open during provider network requests.
7. Use leases and fencing tokens for worker recovery.
8. Use stable provider idempotency keys for retries.
9. Use a shared rate limiter across every worker.
10. Distinguish provider acceptance from actual recipient delivery.
11. Make campaign creation and cancellation idempotent where appropriate.
12. Measure before scaling infrastructure.

## Phase 1: Foundation

Implement:

- Repository structure
- Environment configuration
- Docker Compose
- PostgreSQL
- schema.sql
- SQLAlchemy async engine and session management
- GET /health
- GET /health/db
- Basic tests
- README setup instructions

Create tables:

- campaigns
- campaign_recipients
- messages

Include primary keys, foreign keys, unique constraints, timestamps, status checks and appropriate indexes.

Acceptance criteria:

- PostgreSQL starts successfully.
- Schema initializes successfully.
- API starts successfully.
- Health checks work.
- Tests run and their results are reported accurately.

Stop and verify before Phase 2.

## Phase 2: Campaign API and Bulk Import

Implement:

- POST /api/v1/campaigns
- GET /api/v1/campaigns/{campaign_id}
- Recipient validation
- Chunked bulk inserts
- Campaign creation idempotency
- Accurate campaign counts
- Pagination for large result sets

Never create a partially initialized campaign if the creation transaction fails.

Acceptance criteria:

- Test normal campaign creation.
- Test duplicate recipients.
- Test repeated create requests.
- Test rollback on failure.
- Benchmark bulk import using increasing dataset sizes.

## Phase 3: Mock Provider

Implement a separate mock provider:

- POST /send
- GET /stats
- Durable idempotency-key storage
- Accepted-message timestamps
- Configurable provider failures
- Configurable timeout-after-acceptance
- Reproducible tests

Use the key:

notification:{campaign_id}:{user_id}

The same logical message must use the same key on every retry.

Acceptance criteria:

- Repeated requests with the same key do not create duplicate acceptances.
- Simulated failures and timeout-after-acceptance work as configured.
- Statistics expose accepted messages and timestamps.

## Phase 4: Single Worker

Implement:

- Separate worker entry point
- Eligible-message selection
- Bounded batch claiming
- Scheduled dispatch
- State transitions
- Provider HTTP client
- Graceful shutdown

Claim work transactionally, commit the claim, then make the network request.

Acceptance criteria:

- No message is sent before send_at.
- A successful campaign completes.
- Message states remain consistent.
- Provider calls do not run inside long-lived database transactions.

## Phase 5: Retries and Idempotency

Implement:

- Exponential backoff
- Jitter
- Retry-After handling
- Maximum five attempts
- Permanent versus transient error classification
- Unknown provider outcome handling
- Structured error records

Do not assume that a timeout means the provider rejected the message.

Acceptance criteria:

- Timeout-after-acceptance does not produce duplicate acceptance with the mock provider.
- Exhausted retries become failed.
- Retry scheduling survives worker restart.
- Retry traffic does not overwhelm the provider.

## Phase 6: Multiple Workers and Crash Recovery

Implement:

- Multiple independent worker processes
- PostgreSQL FOR UPDATE SKIP LOCKED
- Lease expiration
- Lease tokens/fencing
- Conditional state updates
- Stale-worker protection
- Recovery of abandoned processing messages

Acceptance criteria:

- Two workers can dispatch concurrently.
- No duplicate logical messages are created.
- Killing a worker does not permanently strand eligible messages.
- Stale workers cannot overwrite newer database state.
- Provider idempotency prevents duplicate acceptance after uncertain outcomes.

## Phase 7: Cancellation and Distributed Rate Limiting

Implement:

- POST /api/v1/campaigns/{campaign_id}/cancel
- Idempotent cancellation
- Explicit cancellation linearization semantics
- Redis-backed atomic rate limiting
- Per-tenant quotas
- Provider-wide quotas
- Bounded concurrency
- Fair scheduling between tenants

Coordinate cancellation with message claims. After cancellation commits, workers must not begin new sends for that campaign. Requests already in flight must retain their actual provider outcomes.

Do not use independent process-local rate-limit counters.

Acceptance criteria:

- Repeated cancellation is safe.
- In-flight requests are not falsely marked cancelled.
- Multiple workers collectively obey configured quotas.
- Test rolling one-second acceptance timestamps if the provider contract makes that guarantee possible.
- Document limitations when acceptance timing cannot be strictly controlled by the client.

## Phase 8: Observability and Operations

Implement:

- Structured logs
- Correlation IDs
- Queue-depth metrics
- Oldest-message age
- Accepted sends per second
- Retry rate
- Provider latency
- Database connection and saturation metrics
- Error alerts
- Backup instructions
- Restore procedure
- Data retention policy

Acceptance criteria:

- Provider outages are observable.
- Stuck backlogs are detectable.
- Database recovery procedures are documented and tested.

## Phase 9: Integration and Load Testing

Run a real integration environment with:

- One API process
- Two separate worker processes
- One mock provider process
- Shared PostgreSQL
- Shared Redis

Test progressively:
1,000 recipients
10,000 recipients
100,000 recipients
1,000,000 recipients

Verify:

- Exactly one accepted provider message per logical message.
- No early sends.
- Retry correctness.
- Timeout-after-acceptance correctness.
- Worker crash recovery.
- Cancellation semantics.
- Accurate database and API counts.
- Tenant rate-limit correctness.
- No duplicate campaign/user message records.
- Bulk import throughput.
- Dispatch throughput.
- p95 latency and queue age.
- Resource usage and bottlenecks.

Do not claim that a million-message test passed unless it actually ran and completed.

## Performance and Scaling Rules

Start with measured defaults rather than assumed production values.

Benchmark:

- Chunked insert size
- Message claim batch size
- Worker concurrency
- Database connection pool size
- Provider request latency
- Rate-limit contention

Consider PgBouncer if connection counts become a problem.

Consider RabbitMQ, SQS, or Kafka only if measured requirements justify introducing a separate broker.

Consider partitioning, reporting counters, or read replicas only after query plans and measurements identify the need.

Do not scale worker count blindly.

## Required Development Workflow

For each phase:

1. Inspect existing code.
2. Implement only the current phase.
3. Run relevant tests.
4. Fix failures.
5. Inspect database state and logs.
6. Review code changes.
7. Update documentation.
8. Report commands actually executed and results.
9. Stop and wait for verification before starting the next phase.

Do not skip phases.
Do not silently skip tests.
Do not claim tests passed without running them.
Do not overwrite unrelated repository files.
Do not generate pseudocode in place of required production code.

## Final Deliverables

- Working source code
- Hand-written schema.sql
- Docker Compose environment
- .env.example
- Automated unit tests
- Real integration tests
- Load-test scripts and results
- README with exact Ubuntu commands
- Architecture and state-transition documentation
- Failure-handling and recovery documentation
- Performance report
- Known limitations and remaining risks

Begin with Phase 1 only. After implementation and verification, stop and report the results. Do not implement later phases until explicitly instructed.
