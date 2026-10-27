from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.campaign import CampaignCreate


def valid_campaign() -> dict:
    return {
        "tenant_id": uuid4(),
        "channel": "sms",
        "send_at": datetime.now(UTC),
        "recipients": [{"user_id": uuid4(), "destination": "+15551234567"}],
    }


def test_campaign_requires_timezone() -> None:
    payload = valid_campaign()
    payload["send_at"] = datetime.now()
    with pytest.raises(ValidationError):
        CampaignCreate(**payload)


def test_blank_destination_is_rejected() -> None:
    payload = valid_campaign()
    payload["recipients"][0]["destination"] = "  "
    with pytest.raises(ValidationError):
        CampaignCreate(**payload)
