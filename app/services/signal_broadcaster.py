"""
Single shared Redis pubsub task that fans 'signals' messages out to all
connected WebSocket clients.

Without this, every WS client opens its own pubsub against Redis. At 100
concurrent users that's 100 connections to the same channel doing the
same work. With it, we have exactly one Redis pubsub and an in-process
asyncio.Queue per client.

The broadcaster task is started lazily on the first subscriber and
stopped automatically when the last one leaves.
"""
import asyncio
import json
import logging
from typing import Optional

from app.redis_client import get_redis

logger = logging.getLogger(__name__)


class _Broadcaster:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        async with self._lock:
            self._subs.add(q)
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._run())
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        async with self._lock:
            self._subs.discard(q)
            if not self._subs and self._task is not None:
                self._task.cancel()
                self._task = None

    async def _run(self) -> None:
        try:
            redis = await get_redis()
            pubsub = redis.pubsub()
            await pubsub.subscribe("signals")
            logger.info("signal broadcaster: redis pubsub started")
            try:
                async for msg in pubsub.listen():
                    if msg.get("type") != "message":
                        continue
                    payload = msg["data"]
                    # Snapshot to avoid mutation while iterating
                    for q in list(self._subs):
                        try:
                            q.put_nowait(payload)
                        except asyncio.QueueFull:
                            # Drop on slow consumers — better than back-pressuring
                            # the entire fanout for everyone.
                            pass
            finally:
                try:
                    await pubsub.unsubscribe("signals")
                    await pubsub.aclose()
                except Exception:
                    pass
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("signal broadcaster crashed: %s", exc)
        finally:
            logger.info("signal broadcaster: redis pubsub stopped")


broadcaster = _Broadcaster()


def parse(payload: str) -> dict | None:
    try:
        return json.loads(payload)
    except Exception:
        return None
