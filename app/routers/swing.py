"""
Swing Strategy endpoints — Dynamic Trend Matrix confluence (4H + 15M).

Free   — list/long/short cache reads (cheap)
Basic  — per-symbol on-demand recompute (heavy)

Cached every 5 min by Celery beat under  swing:{symbol}.
"""

import json
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException

from app.redis_client import get_redis
from app.services.auth import require_plan
from app.services.swing_strategy import run_swing_strategy
from app.utils.symbols import SYMBOLS

router = APIRouter()
logger = logging.getLogger(__name__)

SWING_KEY = "swing:{symbol}"
SWING_TTL = 600   # 10 minutes


async def _all_cached():
    redis = await get_redis()
    pipe = redis.pipeline()
    for sym in SYMBOLS:
        pipe.get(SWING_KEY.format(symbol=sym))
    results = await pipe.execute()
    return [json.loads(r) for r in results if r]


@router.get("/", response_model=List[dict])
async def list_swing():
    """Latest cached swing analysis for all symbols. (free)"""
    return await _all_cached()


@router.get("/signals/long", response_model=List[dict])
async def long_signals():
    """Only LONG swing signals (both 4H and 15M agree up). (free)"""
    rows = await _all_cached()
    rows = [r for r in rows if r.get("signal") == "LONG"]
    rows.sort(key=lambda x: x.get("confidence", 0), reverse=True)
    return rows


@router.get("/signals/short", response_model=List[dict])
async def short_signals():
    """Only SHORT swing signals (both 4H and 15M agree down). (free)"""
    rows = await _all_cached()
    rows = [r for r in rows if r.get("signal") == "SHORT"]
    rows.sort(key=lambda x: x.get("confidence", 0), reverse=True)
    return rows


@router.get("/{symbol}", response_model=dict)
async def get_swing(symbol: str, _principal=Depends(require_plan("basic"))):
    """Compute (or read cached) swing analysis for a single symbol. (basic+)"""
    sym = symbol.upper()
    redis = await get_redis()

    cached = await redis.get(SWING_KEY.format(symbol=sym))
    if cached:
        return json.loads(cached)

    result = await run_swing_strategy(sym)
    if not result:
        raise HTTPException(status_code=503, detail="Insufficient data for swing analysis")

    await redis.set(SWING_KEY.format(symbol=sym), json.dumps(result), ex=SWING_TTL)
    return result
