"""
Layer 4 — Signal Service
─────────────────────────────────────────────────────────────────────────────
Consumes the output of the Strategy Engine and:
  1. Persists the signal record to PostgreSQL.
  2. Caches the latest signal in Redis  →  signal:{SYMBOL}
  3. Publishes to Redis pub/sub channel "signals"  →  consumed by WS router.
"""

import json
import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.db_models import Signal, SignalType
from app.redis_client import get_redis

logger = logging.getLogger(__name__)

SIGNAL_KEY     = "signal:{symbol}"
SIGNAL_CHANNEL = "signals"


async def save_and_publish(result: dict, db: AsyncSession) -> Signal:
    """Persist *result* to PostgreSQL, cache in Redis, and publish for WS fans."""
    record = Signal(
        symbol      = result["symbol"],
        signal      = SignalType(result["signal"]),
        price       = result["price"],
        bb_upper    = result.get("bb_upper"),
        bb_lower    = result.get("bb_lower"),
        interval    = settings.KLINE_INTERVAL,
        created_at  = datetime.utcnow(),
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    payload = {
        **result,
        "id":       record.id,
        "interval": settings.KLINE_INTERVAL,
        "timestamp": record.created_at.isoformat() + "Z",  # explicit UTC so browsers parse correctly
    }

    redis = await get_redis()
    pipe  = redis.pipeline()
    # Cache latest signal for this symbol
    pipe.set(
        SIGNAL_KEY.format(symbol=result["symbol"]),
        json.dumps(payload),
        ex=settings.SIGNAL_CACHE_TTL,
    )
    # Publish to "signals" channel so WebSocket clients receive it instantly
    pipe.publish(SIGNAL_CHANNEL, json.dumps(payload))
    await pipe.execute()

    logger.info("Signal saved & published: %s → %s", result["symbol"], result["signal"])
    return record
