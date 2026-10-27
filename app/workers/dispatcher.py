import asyncio
import json
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
from redis.asyncio import Redis
from sqlalchemy import text

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.logging import logger

RATE_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then redis.call('EXPIRE', KEYS[1], 1) end
if current > tonumber(ARGV[1]) then return 0 end
return 1
"""


@dataclass(frozen=True)
class ClaimedMessage:
    id: UUID
    campaign_id: UUID
    user_id: UUID
    destination: str
    tenant_id: UUID
    attempt: int
    lease_token: UUID


class Worker:
    def __init__(self) -> None:
        self.worker_id = str(uuid4())
        self.redis = Redis.from_url(settings.redis_url, decode_responses=True)
        self.client = httpx.AsyncClient(timeout=settings.provider_timeout_seconds)
        self.semaphore = asyncio.Semaphore(settings.worker_concurrency)

    async def close(self) -> None:
        await self.client.aclose()
        await self.redis.aclose()

    async def run(self, shutdown_event: asyncio.Event) -> None:
        logger.info("worker_started", extra={"worker_id": self.worker_id})
        try:
            while not shutdown_event.is_set():
                claimed = await self.claim_messages()
                if not claimed:
                    await asyncio.sleep(0.25)
                    continue
                await asyncio.gather(
                    *(self._limited_send(message) for message in claimed)
                )
        finally:
            await self.close()

    async def _limited_send(self, message: ClaimedMessage) -> None:
        async with self.semaphore:
            await self.send_message(message)

    async def claim_messages(self) -> list[ClaimedMessage]:
        token = uuid4()
        lease_until = datetime.now(UTC) + timedelta(
            seconds=settings.lease_duration_seconds
        )
        async with AsyncSessionLocal() as db, db.begin():
            rows = await db.execute(
                text("""
                SELECT m.id, m.campaign_id, m.user_id, m.destination, m.attempts, c.tenant_id
                FROM messages m JOIN campaigns c ON c.id=m.campaign_id
                WHERE c.status IN ('scheduled','running') AND c.send_at <= now()
                  AND ((m.status='pending' AND m.next_attempt_at <= now())
                    OR (m.status='processing' AND m.lease_until < now()))
                ORDER BY c.send_at, m.created_at
                FOR UPDATE OF m, c SKIP LOCKED LIMIT :limit
            """),
                {"limit": settings.claim_batch_size},
            )
            data = list(rows.mappings())
            if not data:
                return []
            ids = [row.id for row in data]
            await db.execute(
                text("""
                UPDATE messages SET status='processing', lease_token=:token, lease_until=:lease_until,
                  attempts=attempts+1, updated_at=now() WHERE id = ANY(:ids)
            """),
                {"token": token, "lease_until": lease_until, "ids": ids},
            )
            await db.execute(
                text(
                    "UPDATE campaigns SET status='running', updated_at=now() WHERE id = ANY(:ids) AND status='scheduled'"
                ),
                {"ids": list({row.campaign_id for row in data})},
            )
        return [
            ClaimedMessage(
                id=row.id,
                campaign_id=row.campaign_id,
                user_id=row.user_id,
                destination=row.destination,
                tenant_id=row.tenant_id,
                attempt=row.attempts + 1,
                lease_token=token,
            )
            for row in data
        ]

    async def rate_allowed(self, message: ClaimedMessage) -> bool:
        # Fixed one-second windows are atomic and shared by every worker. This
        # bounds requests started by the client, not provider acceptance time.
        now = int(datetime.now(UTC).timestamp())
        provider = await self.redis.eval(
            RATE_SCRIPT,
            1,
            f"rate:provider:{now}",
            settings.provider_rate_limit_per_second,
        )
        tenant = await self.redis.eval(
            RATE_SCRIPT,
            1,
            f"rate:tenant:{message.tenant_id}:{now}",
            settings.tenant_rate_limit_per_second,
        )
        return bool(provider and tenant)

    async def send_message(self, message: ClaimedMessage) -> None:
        if not await self._still_sendable(message):
            return
        try:
            if not await self.rate_allowed(message):
                await self._retry(message, "rate_limited", delay=1.0)
                return
        except Exception as exc:
            # Redis is required for quota enforcement. Do not send unmetered.
            await self._retry(message, f"rate_limiter_unavailable:{type(exc).__name__}")
            return
        key = f"notification:{message.campaign_id}:{message.user_id}"
        try:
            response = await self.client.post(
                f"{settings.mock_provider_url}/send",
                json={"to": message.destination, "message": "notification"},
                headers={"Idempotency-Key": key},
            )
            if 200 <= response.status_code < 300:
                await self._accepted(message, response.json())
            elif response.status_code in (408, 425, 429) or response.status_code >= 500:
                retry_after = response.headers.get("Retry-After")
                await self._retry(
                    message,
                    f"http_{response.status_code}",
                    delay=float(retry_after) if retry_after else None,
                    http_status=response.status_code,
                )
            else:
                await self._failed(
                    message, f"http_{response.status_code}", response.status_code
                )
        except (httpx.TimeoutException, httpx.NetworkError):
            # Outcome is unknown; stable provider idempotency key makes retry safe.
            await self._retry(message, "unknown_provider_outcome")

    async def _still_sendable(self, message: ClaimedMessage) -> bool:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                text("""SELECT m.status, m.lease_token, c.status AS campaign_status
                FROM messages m JOIN campaigns c ON c.id=m.campaign_id WHERE m.id=:id"""),
                {"id": message.id},
            )
            row = result.mappings().first()
            if (
                row
                and row.status == "processing"
                and row.lease_token == message.lease_token
                and row.campaign_status != "cancelled"
            ):
                return True
            if (
                row
                and row.status == "processing"
                and row.campaign_status == "cancelled"
            ):
                await db.execute(
                    text("""UPDATE messages SET status='cancelled', lease_token=NULL,
                    lease_until=NULL, updated_at=now() WHERE id=:id AND lease_token=:token"""),
                    {"id": message.id, "token": message.lease_token},
                )
                await db.commit()
        return False

    async def _accepted(self, message: ClaimedMessage, payload: dict) -> None:
        async with AsyncSessionLocal() as db, db.begin():
            result = await db.execute(
                text("""UPDATE messages SET status='accepted', provider_message_id=:provider_id,
                accepted_at=now(), lease_token=NULL, lease_until=NULL, updated_at=now()
                WHERE id=:id AND status='processing' AND lease_token=:token"""),
                {
                    "id": message.id,
                    "token": message.lease_token,
                    "provider_id": payload.get("message_id"),
                },
            )
            if result.rowcount:
                await self._attempt(db, message, "accepted")
                await db.execute(
                    text("""UPDATE campaigns c SET status='completed', updated_at=now()
                    WHERE c.id=:campaign_id AND NOT EXISTS (
                      SELECT 1 FROM messages m WHERE m.campaign_id=c.id AND m.status IN ('pending','processing'))"""),
                    {"campaign_id": message.campaign_id},
                )

    async def _retry(
        self,
        message: ClaimedMessage,
        reason: str,
        delay: float | None = None,
        http_status: int | None = None,
    ) -> None:
        exhausted = message.attempt >= settings.max_attempts
        delay = (
            delay
            if delay is not None
            else min(300.0, 2 ** (message.attempt - 1) + random.uniform(0, 1))
        )
        async with AsyncSessionLocal() as db, db.begin():
            if exhausted:
                query = """UPDATE messages SET status='failed', lease_token=NULL, lease_until=NULL,
                    last_error=CAST(:error AS jsonb), updated_at=now() WHERE id=:id AND status='processing' AND lease_token=:token"""
                params = {
                    "id": message.id,
                    "token": message.lease_token,
                    "error": json.dumps({"reason": reason, "retryable": True}),
                }
                outcome = "failed"
            else:
                query = """UPDATE messages SET status='pending', lease_token=NULL, lease_until=NULL,
                    next_attempt_at=now() + (:delay * interval '1 second'), last_error=CAST(:error AS jsonb), updated_at=now()
                    WHERE id=:id AND status='processing' AND lease_token=:token"""
                params = {
                    "id": message.id,
                    "token": message.lease_token,
                    "delay": delay,
                    "error": json.dumps({"reason": reason, "retryable": True}),
                }
                outcome = "retry_scheduled"
            result = await db.execute(text(query), params)
            if result.rowcount:
                await self._attempt(db, message, outcome, http_status, reason)

    async def _failed(
        self, message: ClaimedMessage, reason: str, http_status: int
    ) -> None:
        async with AsyncSessionLocal() as db, db.begin():
            result = await db.execute(
                text("""UPDATE messages SET status='failed', lease_token=NULL, lease_until=NULL,
                last_error=CAST(:error AS jsonb), updated_at=now() WHERE id=:id AND status='processing' AND lease_token=:token"""),
                {
                    "id": message.id,
                    "token": message.lease_token,
                    "error": json.dumps({"reason": reason, "retryable": False}),
                },
            )
            if result.rowcount:
                await self._attempt(db, message, "failed", http_status, reason)

    async def _attempt(
        self,
        db,
        message: ClaimedMessage,
        outcome: str,
        http_status: int | None = None,
        detail: str | None = None,
    ) -> None:
        await db.execute(
            text("""INSERT INTO message_attempts(message_id,attempt_number,outcome,http_status,detail)
            VALUES(:id,:attempt,:outcome,:status,:detail)"""),
            {
                "id": message.id,
                "attempt": message.attempt,
                "outcome": outcome,
                "status": http_status,
                "detail": detail,
            },
        )
