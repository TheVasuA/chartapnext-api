"""
Layer 1 — Binance WebSocket → Redis
─────────────────────────────────────────────────────────────────────────────
Subscribes to Binance combined kline streams for every symbol.
• Each tick  → cached in Redis as  tick:{SYMBOL}      (TTL = SIGNAL_CACHE_TTL)
• Each closed candle → appended to Redis list  ohlcv:{SYMBOL}
  The list is trimmed to the last OHLCV_MAX_CANDLES entries.
• On candle close → publishes {"symbol": "BTCUSDT"} to channel "candle_closed"
  so the strategy engine can react immediately.
"""

import asyncio
import json
import logging
from typing import Optional

import websockets

from app.config import settings
from app.redis_client import get_redis
from app.utils.symbols import SYMBOLS

logger = logging.getLogger(__name__)


class BinanceWSManager:
    def __init__(self) -> None:
        self._running = False
        self._ws: Optional[websockets.WebSocketClientProtocol] = None

    # ------------------------------------------------------------------ #
    #  Public interface                                                    #
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        self._running = True
        while self._running:
            try:
                await self._connect()
            except Exception as exc:
                logger.error("Binance WS error: %s — reconnecting in 5 s", exc)
                await asyncio.sleep(5)

    async def stop(self) -> None:
        self._running = False
        if self._ws:
            await self._ws.close()

    # ------------------------------------------------------------------ #
    #  Internal                                                            #
    # ------------------------------------------------------------------ #

    def _build_url(self) -> str:
        streams = "/".join(
            f"{s.lower()}@kline_{settings.KLINE_INTERVAL}" for s in SYMBOLS
        )
        return f"{settings.BINANCE_WS_URL}?streams={streams}"

    async def _connect(self) -> None:
        url = self._build_url()
        logger.info("Connecting to Binance WS (%d symbols)…", len(SYMBOLS))
        async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
            self._ws = ws
            redis = await get_redis()
            async for raw in ws:
                if not self._running:
                    break
                await self._handle(raw, redis)

    async def _handle(self, raw: str, redis) -> None:
        try:
            msg  = json.loads(raw)
            data = msg.get("data", {})
            k    = data.get("k")
            if not k:
                return

            symbol: str  = k["s"]   # e.g. BTCUSDT
            closed: bool = k["x"]   # True when the candle is finalised

            candle = {
                "symbol":     symbol,
                "open_time":  k["t"],
                "open":       k["o"],
                "high":       k["h"],
                "low":        k["l"],
                "close":      k["c"],
                "volume":     k["v"],
                "close_time": k["T"],
                "closed":     closed,
            }

            # Always keep the latest live tick available
            await redis.set(
                f"tick:{symbol}",
                json.dumps(candle),
                ex=settings.SIGNAL_CACHE_TTL,
            )

            if closed:
                list_key = f"ohlcv:{symbol}"
                await redis.rpush(list_key, json.dumps(candle))
                await redis.ltrim(list_key, -settings.OHLCV_MAX_CANDLES, -1)
                # Notify strategy engine
                await redis.publish("candle_closed", json.dumps({"symbol": symbol}))

        except Exception as exc:
            logger.error("Error processing Binance message: %s", exc)
