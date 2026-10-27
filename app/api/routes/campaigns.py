import json
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.campaign import (
    CampaignCreate,
    CampaignDetailResponse,
    CampaignResponse,
    Recipient,
    RecipientPage,
)

router = APIRouter()
MESSAGE_STATUSES = ("pending", "processing", "accepted", "failed", "cancelled")


def campaign_response(row: object) -> CampaignResponse:
    return CampaignResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        channel=row.channel,
        send_at=row.send_at,
        status=row.status,
        recipient_count=row.recipient_count,
        created_at=row.created_at,
    )


@router.post(
    "/campaigns", response_model=CampaignResponse, status_code=status.HTTP_201_CREATED
)
async def create_campaign(
    campaign: CampaignCreate,
    response: Response,
    idempotency_key: str = Header(
        ..., alias="Idempotency-Key", min_length=1, max_length=255
    ),
    db: AsyncSession = Depends(get_db),
) -> CampaignResponse:
    recipients = campaign.recipients
    if len({r.user_id for r in recipients}) != len(recipients):
        raise HTTPException(status_code=422, detail="duplicate user_id in recipients")
    campaign_id = uuid4()
    try:
        async with db.begin():
            inserted = await db.execute(
                text("""
                INSERT INTO campaigns (id, tenant_id, idempotency_key, channel, payload, send_at, recipient_count)
                VALUES (:id, :tenant_id, :key, :channel, CAST(:payload AS jsonb), :send_at, :count)
                ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
                RETURNING id, tenant_id, channel, send_at, status, recipient_count, created_at
            """),
                {
                    "id": campaign_id,
                    "tenant_id": campaign.tenant_id,
                    "key": idempotency_key,
                    "channel": campaign.channel,
                    "payload": json.dumps(campaign.payload),
                    "send_at": campaign.send_at,
                    "count": len(recipients),
                },
            )
            row = inserted.mappings().first()
            if row is None:
                existing = await db.execute(
                    text("""SELECT id, tenant_id, channel, send_at, status, recipient_count, created_at
                    FROM campaigns WHERE tenant_id=:tenant_id AND idempotency_key=:key"""),
                    {"tenant_id": campaign.tenant_id, "key": idempotency_key},
                )
                response.status_code = status.HTTP_200_OK
                return campaign_response(existing.mappings().one())
            recipient_rows = [
                {
                    "id": uuid4(),
                    "campaign_id": campaign_id,
                    "user_id": r.user_id,
                    "destination": r.destination,
                }
                for r in recipients
            ]
            message_rows = [
                {
                    "id": uuid4(),
                    "campaign_id": campaign_id,
                    "user_id": r.user_id,
                    "destination": r.destination,
                }
                for r in recipients
            ]
            for start in range(0, len(recipients), 5_000):
                await db.execute(
                    text(
                        "INSERT INTO campaign_recipients (id,campaign_id,user_id,destination) VALUES (:id,:campaign_id,:user_id,:destination)"
                    ),
                    recipient_rows[start : start + 5_000],
                )
                await db.execute(
                    text(
                        "INSERT INTO messages (id,campaign_id,user_id,destination) VALUES (:id,:campaign_id,:user_id,:destination)"
                    ),
                    message_rows[start : start + 5_000],
                )
            return campaign_response(row)
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409, detail="campaign creation conflict"
        ) from exc


@router.get("/campaigns/{campaign_id}", response_model=CampaignDetailResponse)
async def get_campaign(
    campaign_id: UUID, db: AsyncSession = Depends(get_db)
) -> CampaignDetailResponse:
    result = await db.execute(
        text(
            "SELECT id, tenant_id, channel, send_at, status, recipient_count, created_at FROM campaigns WHERE id=:id"
        ),
        {"id": campaign_id},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    totals = await db.execute(
        text(
            "SELECT status, count(*) AS total FROM messages WHERE campaign_id=:id GROUP BY status"
        ),
        {"id": campaign_id},
    )
    counts = {state: 0 for state in MESSAGE_STATUSES}
    counts.update({item.status: item.total for item in totals.mappings()})
    return CampaignDetailResponse(**campaign_response(row).model_dump(), counts=counts)


@router.get("/campaigns/{campaign_id}/recipients", response_model=RecipientPage)
async def list_recipients(
    campaign_id: UUID,
    cursor: UUID | None = None,
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
) -> RecipientPage:
    rows = await db.execute(
        text("""SELECT id, user_id, destination FROM campaign_recipients
        WHERE campaign_id=:campaign_id AND (:cursor IS NULL OR id > :cursor) ORDER BY id LIMIT :take"""),
        {"campaign_id": campaign_id, "cursor": cursor, "take": limit + 1},
    )
    values = list(rows.mappings())
    return RecipientPage(
        items=[
            Recipient(user_id=v.user_id, destination=v.destination)
            for v in values[:limit]
        ],
        next_cursor=str(values[-1].id) if len(values) > limit else None,
    )


@router.post("/campaigns/{campaign_id}/cancel")
async def cancel_campaign(
    campaign_id: UUID, db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    # The row lock linearizes cancellation against worker claims. In-flight calls retain their actual outcome.
    async with db.begin():
        found = await db.execute(
            text("SELECT status FROM campaigns WHERE id=:id FOR UPDATE"),
            {"id": campaign_id},
        )
        if found.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Campaign not found")
        await db.execute(
            text(
                "UPDATE campaigns SET status='cancelled', cancelled_at=now(), updated_at=now() WHERE id=:id"
            ),
            {"id": campaign_id},
        )
        await db.execute(
            text("""UPDATE messages SET status='cancelled', lease_until=NULL, lease_token=NULL, updated_at=now()
            WHERE campaign_id=:id AND status='pending'"""),
            {"id": campaign_id},
        )
    return {"status": "cancelled"}
