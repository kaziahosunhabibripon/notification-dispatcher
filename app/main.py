from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from sqlalchemy import text

from app.api.routes import campaigns
from app.core.database import AsyncSessionLocal, close_database


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await close_database()


app = FastAPI(
    title="Scheduled Notification Dispatcher", version="1.0.0", lifespan=lifespan
)
app.include_router(campaigns.router, prefix="/api/v1", tags=["campaigns"])


@app.middleware("http")
async def correlation_id(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid4()))
    started = perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["Server-Timing"] = (
        f"app;dur={((perf_counter() - started) * 1000):.1f}"
    )
    return response


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/db")
async def database_health() -> dict[str, str]:
    async with AsyncSessionLocal() as session:
        await session.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}


@app.get("/metrics/queue")
async def queue_metrics() -> dict:
    """Low-cardinality operational snapshot; scrape/transform in your collector."""
    async with AsyncSessionLocal() as session:
        counts = await session.execute(
            text("SELECT status, count(*) AS total FROM messages GROUP BY status")
        )
        oldest = await session.execute(
            text(
                "SELECT EXTRACT(EPOCH FROM now() - min(created_at)) AS seconds "
                "FROM messages WHERE status='pending'"
            )
        )
        retries = await session.execute(
            text(
                "SELECT count(*) AS total FROM message_attempts "
                "WHERE outcome='retry_scheduled' "
                "AND created_at > now() - interval '1 minute'"
            )
        )
    return {
        "message_counts": {row.status: row.total for row in counts.mappings()},
        "oldest_pending_age_seconds": oldest.scalar_one_or_none(),
        "retries_last_minute": retries.scalar_one(),
    }
