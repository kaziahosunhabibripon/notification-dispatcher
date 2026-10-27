# Scheduled Notification Dispatcher

A durable, multi-worker backend for scheduled notification campaigns. It uses PostgreSQL as the work queue and system of record, Redis for distributed request-rate limiting, and a local mock provider for development and integration testing.

> Provider **acceptance** is tracked by this service. Recipient delivery is a separate provider-side concern.

## Contents

- [What runs](#what-runs)
- [Requirements](#requirements)
- [Quick start with Docker](#quick-start-with-docker)
- [Verify the application](#verify-the-application)
- [Create a campaign](#create-a-campaign)
- [Local development](#local-development)
- [Configuration](#configuration)
- [Testing and code quality](#testing-and-code-quality)
- [Operations](#operations)
- [Delivery model and limitations](#delivery-model-and-limitations)
- [Project layout](#project-layout)

## What runs

| Service | Address | Role |
| --- | --- | --- |
| API | http://localhost:8000 | Campaign API and health endpoints |
| API documentation | http://localhost:8000/docs | Swagger UI |
| Mock provider | http://localhost:8001 | Idempotent test notification provider |
| PostgreSQL | localhost:5432 | Durable campaign, message, and attempt state |
| Redis | localhost:6379 | Shared tenant and provider request quotas |
| Worker 1 / Worker 2 | — | Independently claim and dispatch due messages |

## Requirements

For the recommended setup, install:

- Docker Desktop or Docker Engine with the Compose plugin
- At least 2 GB available RAM for the local stack

For local, non-container development, also install:

- Python 3.12+
- PostgreSQL 16+
- Redis 7+

## Quick start with Docker

From the project root:

```bash
cd /home/ripon/Desktop/whatsapp

# Create local configuration.
cp .env.example .env

# Build and start PostgreSQL, Redis, API, mock provider, and two workers.
docker compose up --build -d

# Wait until services are ready, then inspect their state.
docker compose ps
```

If Docker Desktop is installed but stopped:

```bash
docker desktop start
```

Follow logs while the application starts:

```bash
docker compose logs -f api worker1 worker2 mock-provider
```

Stop services while preserving database data:

```bash
docker compose down
```

Remove services **and all local database data**:

```bash
docker compose down -v
```

## Verify the application

```bash
curl -i http://localhost:8000/health
curl -i http://localhost:8000/health/db
curl -s http://localhost:8001/stats
```

Expected health response:

```json
{"status":"ok"}
```

Useful operational endpoints:

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | API process is running |
| `GET /health/db` | API can query PostgreSQL |
| `GET /metrics/queue` | Current queue counts, oldest pending age, and recent retry count |
| `GET /docs` | Interactive API documentation |
| `GET http://localhost:8001/stats` | Provider acceptance records |

## Create a campaign

Every campaign creation request requires an `Idempotency-Key`. Reuse the same key only when retrying the same logical request.

```bash
curl -X POST http://localhost:8000/api/v1/campaigns \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: onboarding-october-001' \
  -d '{
    "tenant_id": "11111111-1111-1111-1111-111111111111",
    "channel": "sms",
    "send_at": "2030-01-01T00:00:00Z",
    "payload": {"template": "welcome"},
    "recipients": [
      {
        "user_id": "22222222-2222-2222-2222-222222222222",
        "destination": "+15551234567"
      }
    ]
  }'
```

The API returns `201 Created` for a new campaign and `200 OK` for a repeated idempotent request.

Check campaign state:

```bash
curl http://localhost:8000/api/v1/campaigns/<campaign_id>
curl 'http://localhost:8000/api/v1/campaigns/<campaign_id>/recipients?limit=100'
```

Cancel safely (repeat calls are safe):

```bash
curl -X POST http://localhost:8000/api/v1/campaigns/<campaign_id>/cancel
```

## Local development

Use this setup only when you want to run the Python processes outside Docker.

```bash
cd /home/ripon/Desktop/whatsapp
python3.12 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

Start database dependencies with Docker:

```bash
docker compose up -d postgres redis
```

The Docker PostgreSQL container automatically initializes `schema.sql` on its first start. If you use your own empty PostgreSQL database:

```bash
psql -h localhost -U user -d notifications -f schema.sql
```

Start each application process in a separate terminal:

```bash
# Terminal 1
source venv/bin/activate
uvicorn mock_provider.main:app --host 0.0.0.0 --port 8001 --reload

# Terminal 2
source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 3 and 4
source venv/bin/activate
python -m app.worker
```

## Configuration

Copy `.env.example` to `.env`; never commit production credentials.

| Variable | Default | Description |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+asyncpg://user:password@localhost:5432/notifications` | PostgreSQL async connection URL |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `MOCK_PROVIDER_URL` | `http://localhost:8001` | Provider base URL |
| `CLAIM_BATCH_SIZE` | `100` | Maximum messages claimed per worker transaction |
| `WORKER_CONCURRENCY` | `20` | Maximum in-flight provider calls per worker |
| `LEASE_DURATION_SECONDS` | `30` | Processing lease before abandoned work can be reclaimed |
| `MAX_ATTEMPTS` | `5` | Maximum provider attempts for a message |
| `PROVIDER_TIMEOUT_SECONDS` | `5` | HTTP provider timeout |
| `TENANT_RATE_LIMIT_PER_SECOND` | `100` | Shared request-start quota per tenant |
| `PROVIDER_RATE_LIMIT_PER_SECOND` | `1000` | Shared request-start quota for all tenants |

Tune these values from measured queue age, provider latency, and error rates; do not increase worker count blindly.

## Testing and code quality

```bash
source venv/bin/activate

# Unit tests
pytest -q tests/test_unit.py

# Full test suite. Integration tests require the Docker stack to be running.
docker compose up -d
pytest -q

# Static checks
ruff format .
ruff check .
```

Run a small explicit load-import benchmark:

```bash
python tests/load_test.py --recipients 1000
```

Do not interpret this helper as proof of million-recipient capacity. Run progressively sized tests (1k, 10k, 100k, then 1m) on appropriately sized infrastructure and record throughput, p95 latency, queue age, and resource use.

## Operations

Inspect message state directly:

```bash
docker compose exec postgres psql -U user -d notifications -c \
  "SELECT status, count(*) FROM messages GROUP BY status ORDER BY status;"
```

Create a logical backup:

```bash
docker compose exec -T postgres pg_dump -U user notifications > notifications.sql
```

Restore into an empty database:

```bash
cat notifications.sql | docker compose exec -T postgres psql -U user -d notifications
```

Archive `messages`, `message_attempts`, and provider acceptance records according to your data-retention and privacy requirements before purging them.

## Delivery model and limitations

- PostgreSQL is authoritative for campaign/message state.
- Workers claim bounded batches with `FOR UPDATE SKIP LOCKED`, commit the claim, then call the provider outside the transaction.
- Each logical notification uses the stable provider key `notification:{campaign_id}:{user_id}`. The mock provider persists those keys to deduplicate retries.
- Expired leases enable recovery after a worker crash; lease-token conditional updates protect against stale workers.
- A network timeout has an unknown provider outcome, so it is retried with the same idempotency key.
- Cancellation is linearized by a campaign row lock. After cancellation commits, workers will not begin a new provider request; calls already in flight keep their real outcome.
- Redis quotas control distributed **request starts**. Client-side control cannot strictly guarantee provider acceptance timestamps if provider latency varies.
- Exactly-once delivery across a database and an external HTTP provider is not atomic. This design achieves effectively-once provider acceptance when the provider honors durable idempotency keys.

## Project layout

```text
.
├── app/
│   ├── api/routes/          # Campaign endpoints
│   ├── core/                # Configuration, database, JSON logging
│   ├── schemas/             # Request and response models
│   ├── workers/             # Durable dispatcher
│   ├── main.py              # FastAPI application
│   └── worker.py            # Worker entry point
├── mock_provider/           # Development provider service
├── tests/                   # Unit, integration, and load-test helper
├── docker-compose.yml       # Local multi-service environment
├── schema.sql               # Hand-written PostgreSQL schema
└── .env.example             # Configuration template
```
# notification-dispatcher
