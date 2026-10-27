import asyncio
import signal

from app.workers.dispatcher import Worker

shutdown_event = asyncio.Event()


def signal_handler(signum, frame):
    print(f"Received signal {signum}, shutting down...")
    shutdown_event.set()


async def main():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    worker = Worker()
    await worker.run(shutdown_event)


if __name__ == "__main__":
    asyncio.run(main())
