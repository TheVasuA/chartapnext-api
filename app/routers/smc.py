"""
SMC endpoints.

GET  /smc/              — latest SMC analysis for ALL symbols (from Redis cache)
GET  /smc/{symbol}      — live SMC analysis for ONE symbol (computed on-demand)
GET  /smc/signals/long  — only LONG signals
GET  /smc/signals/short — only SHORT signals
"""

import asyncio
import json
import logging
from typing import List

from fastapi import APIRouter, HTTPException

from app.redis_client import get_redis
from app.services.smc_strategy import run_smc_strategy
from app.utils.symbols import SYMBOLS

router = APIRouter()
logger = logging.getLogger(__name__)

SMC_KEY = "smc:{symbol}"
SMC_TTL = 300   # 5 minutes — SMC is 15M timeframe, no need to spam


@router.get("/", response_model=List[dict])
async def list_smc():
    """Return the latest cached SMC analysis for all symbols."""
    redis = await get_redis()
    pipe  = redis.pipeline()
    for sym in SYMBOLS:
        pipe.get(SMC_KEY.format(symbol=sym))
    results = await pipe.execute()
    return [json.loads(r) for r in results if r]


@router.get("/signals/long", response_model=List[dict])
async def long_signals():
    """Return only LONG signals."""
    redis = await get_redis()
    pipe  = redis.pipeline()
    for sym in SYMBOLS:
        pipe.get(SMC_KEY.format(symbol=sym))
    results = await pipe.execute()
    return [json.loads(r) for r in results if r and json.loads(r).get("signal") == "LONG"]


@router.get("/signals/short", response_model=List[dict])
async def short_signals():
    """Return only SHORT signals."""
    redis = await get_redis()
    pipe  = redis.pipeline()
    for sym in SYMBOLS:
        pipe.get(SMC_KEY.format(symbol=sym))
    results = await pipe.execute()
    return [json.loads(r) for r in results if r and json.loads(r).get("signal") == "SHORT"]


@router.get("/{symbol}", response_model=dict)
async def get_smc(symbol: str):
    """Compute and cache SMC analysis for a single symbol."""
    sym   = symbol.upper()
    redis = await get_redis()

    # Return from cache if fresh
    cached = await redis.get(SMC_KEY.format(symbol=sym))
    if cached:
        return json.loads(cached)

    result = await run_smc_strategy(sym)
    if not result:
        raise HTTPException(status_code=503, detail="Insufficient data for SMC analysis")

    await redis.set(SMC_KEY.format(symbol=sym), json.dumps(result), ex=SMC_TTL)
    return result
