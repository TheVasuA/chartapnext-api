"""
Single-instance Binance WebSocket ingestor.

Runs the BinanceWSManager outside the API container so the api service
can scale horizontally without each replica opening its own Binance
connection (which would duplicate work and race on Redis writes).

Deployed as the `ingestor` service in docker-compose. Keep replicas=1.
"""
import asyncio
import logging
import signal

from app.services.binance_ws import BinanceWSManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def main():
    manager = BinanceWSManager()
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass  # Windows / non-Unix

    task = asyncio.create_task(manager.start())
    await stop_event.wait()
    await manager.stop()
    task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
