"""Small, explicit load generator; never reports success unless it completes."""

import argparse
import asyncio
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx


async def main(count: int, url: str) -> None:
    recipients = [
        {"user_id": str(uuid4()), "destination": f"+1555{i:07d}"} for i in range(count)
    ]
    payload = {
        "tenant_id": str(uuid4()),
        "channel": "sms",
        "send_at": (datetime.now(UTC) + timedelta(seconds=10)).isoformat(),
        "recipients": recipients,
    }
    start = time.perf_counter()
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"{url}/api/v1/campaigns",
            json=payload,
            headers={"Idempotency-Key": str(uuid4())},
        )
        response.raise_for_status()
    print(
        {
            "recipients": count,
            "seconds": round(time.perf_counter() - start, 3),
            "campaign": response.json()["id"],
        }
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--recipients", type=int, required=True)
    parser.add_argument("--url", default="http://localhost:8000")
    args = parser.parse_args()
    asyncio.run(main(args.recipients, args.url))
