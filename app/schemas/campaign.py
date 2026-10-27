from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Recipient(BaseModel):
    user_id: UUID
    destination: str = Field(min_length=1, max_length=512)

    @field_validator("destination")
    @classmethod
    def destination_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("destination must not be blank")
        return value.strip()


class CampaignCreate(BaseModel):
    tenant_id: UUID
    channel: str = Field(pattern="^(sms|whatsapp|email|push)$")
    send_at: datetime
    recipients: list[Recipient] = Field(min_length=1, max_length=1_000_000)
    payload: dict = Field(default_factory=dict)

    @field_validator("send_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("send_at must include a timezone")
        return value


class CampaignResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    tenant_id: UUID
    channel: str
    send_at: datetime
    status: str
    recipient_count: int
    created_at: datetime


class CampaignDetailResponse(CampaignResponse):
    counts: dict[str, int]


class RecipientPage(BaseModel):
    items: list[Recipient]
    next_cursor: str | None = None
