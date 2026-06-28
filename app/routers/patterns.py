"""
Chart-pattern endpoints.

Cache reads (list / signals) are light and public so the dashboard stays
snappy. Per-symbol on-demand recompute is gated to Basic (it runs the full
swing-detection + geometric matching and returns the candle window for
drawing).

Cached every 5 min by Celery beat under  patterns:{symbol}:{interval}.
"""

import json
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query

from app.redis_client import get_redis
from app.services.auth import require_plan
from app.services.pattern_detection import run_pattern_scan
from app.utils.symbols import SYMBOLS

router = APIRouter()
logger = logging.getLogger(__name__)

KEY = "patterns:{symbol}:{interval}"
TTL = 900
DEFAULT_INTERVAL = "1h"
ALLOWED_INTERVALS = {"1h", "4h"}


def _norm_interval(interval: str) -> str:
    return interval if interval in ALLOWED_INTERVALS else DEFAULT_INTERVAL


@router.get("/", response_model=List[dict])
async def list_patterns(interval: str = Query(DEFAULT_INTERVAL)):
    """Full cached pattern docs (incl. candle window) for symbols with hits."""
    interval = _norm_interval(interval)
    redis = await get_redis()
    pipe = redis.pipeline()
    for sym in SYMBOLS:
        pipe.get(KEY.format(symbol=sym, interval=interval))
    results = await pipe.execute()

    out = []
    for raw in results:
        if not raw:
            continue
        doc = json.loads(raw)
        if doc.get("patterns"):
            out.append(doc)
    return out


@router.get("/signals", response_model=List[dict])
async def pattern_signals(interval: str = Query(DEFAULT_INTERVAL)):
    """Lightweight feed: the single best pattern per symbol, sorted by
    confidence. Includes the candle window so cards can draw a snapshot."""
    interval = _norm_interval(interval)
    redis = await get_redis()
    pipe = redis.pipeline()
    for sym in SYMBOLS:
        pipe.get(KEY.format(symbol=sym, interval=interval))
    results = await pipe.execute()

    out = []
    for raw in results:
        if not raw:
            continue
        doc = json.loads(raw)
        pats = doc.get("patterns") or []
        if not pats:
            continue
        best = pats[0]
        out.append({
            "symbol": doc["symbol"],
            "interval": doc["interval"],
            "timestamp": doc["timestamp"],
            "window": doc.get("window"),
            **best,
        })
    out.sort(key=lambda x: x.get("confidence", 0), reverse=True)
    return out


@router.get("/{symbol}", response_model=dict)
async def get_pattern(
    symbol: str,
    interval: str = Query(DEFAULT_INTERVAL),
    _principal=Depends(require_plan("basic")),
):
    """Compute (or read cached) pattern analysis for a single symbol. (basic)"""
    sym = symbol.upper()
    interval = _norm_interval(interval)
    redis = await get_redis()

    cached = await redis.get(KEY.format(symbol=sym, interval=interval))
    if cached:
        return json.loads(cached)

    result = await run_pattern_scan(sym, interval=interval)
    if not result:
        raise HTTPException(status_code=503, detail="Insufficient data for pattern analysis")

    await redis.set(KEY.format(symbol=sym, interval=interval), json.dumps(result), ex=TTL)
    return result
