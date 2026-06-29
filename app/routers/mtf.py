"""
Multi-Timeframe confluence endpoints (RSI + MA + MACD across 4H/1H/15M).

Cache reads (list / signals) are light and public so the dashboard stays
snappy. Per-symbol on-demand recompute runs the full 3-timeframe analysis
and is gated to Basic.

Cached every 5 min by Celery beat under  mtf:{symbol}.
"""

import json
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException

from app.redis_client import get_redis
from app.services.auth import require_plan
from app.services.mtf_strategy import run_mtf_strategy
from app.utils.symbols import SYMBOLS

router = APIRouter()
logger = logging.getLogger(__name__)

KEY = "mtf:{symbol}"
TTL = 600


async def _read_all() -> list[dict]:
    redis = await get_redis()
    pipe = redis.pipeline()
    for sym in SYMBOLS:
        pipe.get(KEY.format(symbol=sym))
    results = await pipe.execute()
    return [json.loads(r) for r in results if r]


@router.get("/", response_model=List[dict])
async def list_mtf():
    """Latest cached MTF analysis for all symbols."""
    return await _read_all()


@router.get("/signals/long", response_model=List[dict])
async def long_signals():
    rows = await _read_all()
    rows = [r for r in rows if r.get("signal") == "LONG"]
    rows.sort(key=lambda r: r.get("confidence", 0), reverse=True)
    return rows


@router.get("/signals/short", response_model=List[dict])
async def short_signals():
    rows = await _read_all()
    rows = [r for r in rows if r.get("signal") == "SHORT"]
    rows.sort(key=lambda r: r.get("confidence", 0), reverse=True)
    return rows


@router.get("/{symbol}", response_model=dict)
async def get_mtf(symbol: str, _principal=Depends(require_plan("basic"))):
    """Compute (or read cached) MTF analysis for a single symbol. (basic)"""
    sym = symbol.upper()
    redis = await get_redis()

    cached = await redis.get(KEY.format(symbol=sym))
    if cached:
        return json.loads(cached)

    result = await run_mtf_strategy(sym)
    if not result:
        raise HTTPException(status_code=503, detail="Insufficient data for MTF analysis")

    await redis.set(KEY.format(symbol=sym), json.dumps(result), ex=TTL)
    return result
