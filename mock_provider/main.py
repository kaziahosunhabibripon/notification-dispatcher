from datetime import UTC, datetime
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import text

from app.core.database import AsyncSessionLocal

app = FastAPI(title="Durable Mock Provider")
failure_mode = {"failures_remaining": 0, "timeout_after_acceptance_remaining": 0}


class SendRequest(BaseModel):
    to: str
    message: str


class FailureConfig(BaseModel):
    failures: int = 0
    timeout_after_acceptance: int = 0


@app.post("/send")
async def send_message(
    request: SendRequest, idempotency_key: str = Header(..., alias="Idempotency-Key")
):
    async with AsyncSessionLocal() as db, db.begin():
        existing = await db.execute(
            text(
                "SELECT message_id, accepted_at FROM provider_acceptances WHERE idempotency_key=:key"
            ),
            {"key": idempotency_key},
        )
        row = existing.mappings().first()
        if row is None:
            if failure_mode["failures_remaining"] > 0:
                failure_mode["failures_remaining"] -= 1
                raise HTTPException(
                    status_code=503, detail="configured transient failure"
                )
            message_id, accepted_at = uuid4(), datetime.now(UTC)
            await db.execute(
                text("""INSERT INTO provider_acceptances(idempotency_key,message_id,accepted_at,request)
                VALUES(:key,:id,:accepted_at,CAST(:request AS jsonb))"""),
                {
                    "key": idempotency_key,
                    "id": message_id,
                    "accepted_at": accepted_at,
                    "request": request.model_dump_json(),
                },
            )
            timeout = failure_mode["timeout_after_acceptance_remaining"] > 0
            if timeout:
                failure_mode["timeout_after_acceptance_remaining"] -= 1
        else:
            message_id, accepted_at, timeout = row.message_id, row.accepted_at, False
    if timeout:
        raise HTTPException(
            status_code=504, detail="configured timeout after acceptance"
        )
    return {
        "message_id": str(message_id),
        "status": "accepted",
        "accepted_at": accepted_at.isoformat(),
    }


@app.get("/stats")
async def stats() -> dict:
    async with AsyncSessionLocal() as db:
        results = await db.execute(
            text(
                "SELECT idempotency_key, message_id, accepted_at FROM provider_acceptances ORDER BY accepted_at"
            )
        )
        accepted = [
            {
                "idempotency_key": r.idempotency_key,
                "message_id": str(r.message_id),
                "accepted_at": r.accepted_at.isoformat(),
            }
            for r in results.mappings()
        ]
    return {"count": len(accepted), "accepted_messages": accepted}


@app.post("/configure")
async def configure(config: FailureConfig) -> dict:
    failure_mode.update(
        {
            "failures_remaining": config.failures,
            "timeout_after_acceptance_remaining": config.timeout_after_acceptance,
        }
    )
    return failure_mode


@app.post("/reset", status_code=204)
async def reset() -> Response:
    async with AsyncSessionLocal() as db, db.begin():
        await db.execute(text("DELETE FROM provider_acceptances"))
    failure_mode.update(
        {"failures_remaining": 0, "timeout_after_acceptance_remaining": 0}
    )
    return Response(status_code=204)
