"""
GET /scalping/signals
─────────────────────
Returns the latest consolidation-breakout scan results from Redis.
Refreshed every 2 min by Celery beat. On a cold cache the on-demand scan
is heavy — gated to Pro plans. When the cache is warm, the response is
served free of charge so the public dashboard remains snappy.
"""

import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException

from app.redis_client import get_redis
from app.services.auth import get_principal_optional, PLAN_RANK
from app.services.scalping_strategy import run_scalping_scan

router = APIRouter()
logger = logging.getLogger(__name__)

CACHE_KEY = "scalping:signals"
CACHE_TTL = 300


@router.get("/signals")
async def get_scalping_signals(principal=Depends(get_principal_optional)):
    """Return the latest EMA25 consolidation breakout signals."""
    redis = await get_redis()
    raw   = await redis.get(CACHE_KEY)

    if raw:
        data = json.loads(raw)
        data["source"] = "cache"
        return data

    # Cold cache: only Pro users may trigger the 60-symbol scan to keep latency
    # acceptable for everyone else. Free/Basic users get an empty response and
    # a small message asking them to retry shortly.
    if principal is None or PLAN_RANK[principal.plan] < PLAN_RANK["pro"]:
        return {
            "signals": [],
            "scannedCount": 0,
            "timestamp": int(time.time() * 1000),
            "source": "warming",
            "message": "Scalping scanner is warming up. Try again in 30s, or upgrade to Pro to force a scan.",
        }

    logger.info("scalping signals cache cold — running on-demand scan (pro user)")
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
