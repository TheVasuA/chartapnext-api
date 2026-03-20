"""
Layer 2 — Market Data Service
─────────────────────────────────────────────────────────────────────────────
Primary source : Redis list  ohlcv:{SYMBOL}   (populated by Binance WS)
Fallback       : Binance REST /api/v3/klines  (seeds Redis when list is cold)
"""

import json
import logging
from typing import List

import httpx

from app.config import settings
from app.redis_client import get_redis

logger = logging.getLogger(__name__)
BINANCE_REST = "https://api.binance.com/api/v3/klines"


async def get_ohlcv(symbol: str, limit: int = 200) -> List[dict]:
    """Return OHLCV candles for *symbol*, using Redis cache when warm."""
    redis    = await get_redis()
    list_key = f"ohlcv:{symbol}"
    raw_list = await redis.lrange(list_key, -limit, -1)

    if len(raw_list) >= 50:                      # cache is warm enough
        return [json.loads(c) for c in raw_list]

    logger.info("%s: cache cold (%d candles) — fetching from REST", symbol, len(raw_list))
    return await _fetch_and_cache(symbol, limit)


async def _fetch_and_cache(symbol: str, limit: int = 200) -> List[dict]:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            BINANCE_REST,
            params={"symbol": symbol, "interval": settings.KLINE_INTERVAL, "limit": limit},
        )
        resp.raise_for_status()
        klines = resp.json()

    candles = [
        {
            "symbol":     symbol,
            "open_time":  k[0],
            "open":       k[1],
            "high":       k[2],
            "low":        k[3],
            "close":      k[4],
            "volume":     k[5],
            "close_time": k[6],
            "closed":     True,
        }
        for k in klines
    ]

    if candles:
        redis    = await get_redis()
        list_key = f"ohlcv:{symbol}"
        pipe     = redis.pipeline()
        pipe.delete(list_key)
        for c in candles:
            pipe.rpush(list_key, json.dumps(c))
        pipe.ltrim(list_key, -limit, -1)
        await pipe.execute()

    return candles


async def get_ohlcv_multi(
    symbol: str,
    intervals: list[str],
    limits: list[int],
) -> list[list[dict]]:
    """Fetch OHLCV for multiple timeframes for the same symbol.

    Uses Redis key  ohlcv:{symbol}:{interval}  for each timeframe.
    Falls back to Binance REST when cache is cold.
    """
    results = []
    for interval, limit in zip(intervals, limits):
        candles = await _get_ohlcv_tf(symbol, interval, limit)
        results.append(candles)
    return results


async def _get_ohlcv_tf(symbol: str, interval: str, limit: int = 200) -> list[dict]:
    """Like get_ohlcv() but keyed by interval, fetched from REST directly."""
    # Check dedicated key first (Binance WS only populates the default interval)
    list_key = f"ohlcv:{symbol}:{interval}"
    redis    = await get_redis()
    raw_list = await redis.lrange(list_key, -limit, -1)

    if len(raw_list) >= min(50, limit):
        return [json.loads(c) for c in raw_list]

    # Fetch from REST and cache
    logger.info("%s [%s]: cache cold — fetching from REST", symbol, interval)
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            BINANCE_REST,
            params={"symbol": symbol, "interval": interval, "limit": limit},
        )
        resp.raise_for_status()
        klines = resp.json()

    candles = [
        {
            "symbol":     symbol,
            "open_time":  k[0],
            "open":       k[1],
            "high":       k[2],
            "low":        k[3],
            "close":      k[4],
            "volume":     k[5],
            "close_time": k[6],
            "closed":     True,
        }
        for k in klines
    ]

    if candles:
        pipe = redis.pipeline()
        pipe.delete(list_key)
        for c in candles:
            pipe.rpush(list_key, json.dumps(c))
        pipe.ltrim(list_key, -limit, -1)
        pipe.expire(list_key, 300)   # 5 min TTL — REST data for higher TFs refreshes slower
        await pipe.execute()

    return candles
