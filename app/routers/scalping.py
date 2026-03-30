"""
GET /scalping/signals
─────────────────────
Returns the latest consolidation-breakout scan results from Redis.
Results are refreshed every 2 minutes by the `refresh_scalping_signals` Celery beat task.

If Redis is cold (no cached data yet), triggers an on-demand scan automatically.
"""

import json
import logging

from fastapi import APIRouter, HTTPException

from app.redis_client import get_redis
from app.services.scalping_strategy import run_scalping_scan

import time

router = APIRouter()
logger = logging.getLogger(__name__)

CACHE_KEY = "scalping:signals"
CACHE_TTL = 300   # 5 min


@router.get("/signals")
async def get_scalping_signals():
    """Return the latest EMA25 consolidation breakout signals (cached by Celery beat)."""
    redis = await get_redis()
    raw   = await redis.get(CACHE_KEY)

    if raw:
        data = json.loads(raw)
        data["source"] = "cache"
        return data

    # Cache cold — run on-demand scan once and populate Redis
    logger.info("scalping signals cache cold — running on-demand scan")
    try:
        signals = await run_scalping_scan()
    except Exception as exc:
        logger.error("on-demand scalping scan failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    payload = {
        "signals":      signals,
        "scannedCount": 60,
        "timestamp":    int(time.time() * 1000),
        "source":       "on-demand",
    }
    await redis.set(CACHE_KEY, json.dumps(payload), ex=CACHE_TTL)
    return payload
