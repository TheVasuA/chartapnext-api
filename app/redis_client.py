import asyncio

import redis.asyncio as aioredis

from app.config import settings

_redis: aioredis.Redis | None = None
_redis_loop: asyncio.AbstractEventLoop | None = None


async def get_redis() -> aioredis.Redis:
    """Return a Redis client valid for the current event loop.

    Celery workers create a new event loop per task via asyncio.new_event_loop().
    Reusing a Redis connection from a previous loop causes 'Future attached to a
    different loop' errors, so we recreate the client whenever the loop changes.
    """
    global _redis, _redis_loop
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if _redis is None or _redis_loop is not current_loop:
        if _redis is not None:
            try:
                await _redis.aclose()
            except Exception:
                pass
        _redis = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )
        _redis_loop = current_loop

    return _redis
