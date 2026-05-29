"""
RSI Pullback Strategy endpoints.

Free   — list/long/short cache reads (cheap)
Basic  — per-symbol on-demand recompute (heavy)
"""

import json
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException

from app.redis_client import get_redis
from app.services.auth import require_plan
from app.services.rsi_strategy import run_rsi_strategy
from app.utils.symbols import SYMBOLS

router = APIRouter()
logger = logging.getLogger(__name__)

RSI_KEY = "rsi:{symbol}"
RSI_TTL = 300   # 5 minutes


@router.get("/", response_model=List[dict])
async def list_rsi():
    """Return the latest cached RSI analysis for all symbols. (free)"""
    redis = await get_redis()
    pipe  = redis.pipeline()
    for sym in SYMBOLS:
        pipe.get(RSI_KEY.format(symbol=sym))
    results = await pipe.execute()
    return [json.loads(r) for r in results if r]


@router.get("/signals/long", response_model=List[dict])
async def long_signals():
    """Return only LONG RSI pullback signals. (free)"""
    redis = await get_redis()
    pipe  = redis.pipeline()
    for sym in SYMBOLS:
        pipe.get(RSI_KEY.format(symbol=sym))
    results = await pipe.execute()
    return [json.loads(r) for r in results if r and json.loads(r).get("signal") == "LONG"]


@router.get("/signals/short", response_model=List[dict])
async def short_signals():
    """Return only SHORT RSI pullback signals. (free)"""
    redis = await get_redis()
    pipe  = redis.pipeline()
    for sym in SYMBOLS:
        pipe.get(RSI_KEY.format(symbol=sym))
    results = await pipe.execute()
    return [json.loads(r) for r in results if r and json.loads(r).get("signal") == "SHORT"]


@router.get("/{symbol}", response_model=dict)
async def get_rsi(symbol: str, _principal=Depends(require_plan("basic"))):
    """Compute and cache RSI pullback analysis for a single symbol. (basic+)"""
    sym   = symbol.upper()
    redis = await get_redis()

    cached = await redis.get(RSI_KEY.format(symbol=sym))
    if cached:
        return json.loads(cached)

    result = await run_rsi_strategy(sym)
    if not result:
        raise HTTPException(status_code=503, detail="Insufficient data for RSI analysis")

    await redis.set(RSI_KEY.format(symbol=sym), json.dumps(result), ex=RSI_TTL)
    return result
