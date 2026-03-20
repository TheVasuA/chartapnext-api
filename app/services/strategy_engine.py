"""
Layer 3 — Strategy Engine
─────────────────────────────────────────────────────────────────────────────
Reads OHLCV from Redis (via Market Data Service) → computes indicators
→ applies scoring rules → returns BUY / SELL / HOLD.

Scoring (2-of-3 majority votes):
  BUY  : RSI < 35  AND/OR  MACD histogram > 0  AND/OR  price < BB lower
  SELL : RSI > 65  AND/OR  MACD histogram < 0  AND/OR  price > BB upper
  HOLD : everything else
"""

import logging
from typing import Optional

from app.services.indicators import build_dataframe, compute_indicators, safe_float
from app.services.market_data import get_ohlcv

logger = logging.getLogger(__name__)


async def run_strategy(symbol: str) -> Optional[dict]:
    """Compute a trading signal for *symbol*.  Returns None if not enough data."""
    candles = await get_ohlcv(symbol, limit=200)
    if len(candles) < 60:
        logger.warning("%s: insufficient candles (%d)", symbol, len(candles))
        return None

    df   = build_dataframe(candles)
    df   = compute_indicators(df)
    last = df.iloc[-1]

    rsi       = safe_float(last.get("rsi"))
    macd_val  = safe_float(last.get("macd"))
    macd_sig  = safe_float(last.get("macd_signal"))
    macd_hist = safe_float(last.get("macd_hist"))
    bb_upper  = safe_float(last.get("bb_upper"))
    bb_lower  = safe_float(last.get("bb_lower"))
    price     = safe_float(last.get("close"))

    signal = _score(rsi, macd_hist, price, bb_upper, bb_lower)

    return {
        "symbol":      symbol,
        "signal":      signal,
        "price":       price,
        "rsi":         rsi,
        "macd":        macd_val,
        "macd_signal": macd_sig,
        "bb_upper":    bb_upper,
        "bb_lower":    bb_lower,
    }


def _score(
    rsi:       Optional[float],
    macd_hist: Optional[float],
    price:     Optional[float],
    bb_upper:  Optional[float],
    bb_lower:  Optional[float],
) -> str:
    if (
        rsi is None
        or macd_hist is None
        or price is None
        or bb_upper is None
        or bb_lower is None
    ):
        return "HOLD"

    buy_score  = 0
    sell_score = 0

    if rsi < 35:
        buy_score += 1
    elif rsi > 65:
        sell_score += 1

    if macd_hist > 0:
        buy_score += 1
    elif macd_hist < 0:
        sell_score += 1

    if price < bb_lower:
        buy_score += 1
    elif price > bb_upper:
        sell_score += 1

    if buy_score >= 2:
        return "BUY"
    if sell_score >= 2:
        return "SELL"
    return "HOLD"
