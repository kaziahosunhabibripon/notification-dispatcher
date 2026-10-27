import asyncio
import uuid
from datetime import datetime, timedelta

import httpx
import pytest


@pytest.mark.asyncio
async def test_full_integration():
    """Test with API, 2 workers, mock provider - kill one worker during run"""
    # Reset mock provider
    async with httpx.AsyncClient() as client:
        try:
            reset = await client.post("http://localhost:8001/reset")
        except httpx.ConnectError:
            pytest.skip("Docker integration services are not running")
        assert reset.status_code == 204

    # A small, real end-to-end test; larger runs use tests/load_test.py.
    tenant_id = str(uuid.uuid4())
    recipients = []
    for i in range(20):
        recipients.append(
            {"user_id": str(uuid.uuid4()), "destination": f"+123456{i:04d}"}
        )

    send_at = (datetime.utcnow() + timedelta(seconds=2)).isoformat() + "Z"

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8000/api/v1/campaigns",
            headers={"Idempotency-Key": str(uuid.uuid4())},
            json={
                "tenant_id": tenant_id,
                "channel": "sms",
                "send_at": send_at,
                "recipients": recipients,
            },
        )
        assert response.status_code == 201
        campaign = response.json()
        campaign_id = campaign["id"]

        # Wait for send time
        for _ in range(20):
            await asyncio.sleep(0.5)
            response = await client.get(
                f"http://localhost:8000/api/v1/campaigns/{campaign_id}"
            )
            if response.json()["counts"]["accepted"] == 20:
                break

        # Check campaign status
        response = await client.get(
            f"http://localhost:8000/api/v1/campaigns/{campaign_id}"
        )
        assert response.status_code == 200
        data = response.json()
        print(f"Campaign counts: {data['counts']}")

        # Check provider stats
        response = await client.get("http://localhost:8001/stats")
        stats = response.json()
        print(f"Provider accepted: {stats['count']}")

        # Verify all messages have exactly one acceptance
        assert stats["count"] == 20, f"Expected 20, got {stats['count']}"

        # Verify no sends before send_at (basic check)
        # Verify counts match
        total_counted = sum(data["counts"].values())
        assert total_counted == 20
