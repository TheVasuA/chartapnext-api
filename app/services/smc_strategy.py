"""
SMC (Smart Money Concepts) Strategy Engine
─────────────────────────────────────────────────────────────────────────────
Multi-timeframe analysis:
  • 4H  → trend_4h()       : EMA25 vs EMA99 → bullish / bearish / neutral
  • 1H  → liquidity_sweep() : sweep of prev candle high/low → direction
  • 15M → entry_confirmation(): engulfing candle after sweep aligns with trend

Output shape:
  {
    "symbol":  "ETHUSDT",
    "trend":   "4H bullish",
    "sweep":   "1H bullish_sweep",
    "entry":   "15M LONG",
    "signal":  "LONG",          # LONG | SHORT | WAIT
    "price":   2345.6,
    "tf_4h_ema25": ...,
    "tf_4h_ema99": ...,
  }
"""

import logging
from typing import Optional

import pandas as pd

from app.services.indicators import build_dataframe, safe_float
from app.services.market_data import get_ohlcv_multi

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Sub-indicators
# ─────────────────────────────────────────────────────────────────────────────

def trend_4h(df: pd.DataFrame) -> tuple[str, float | None, float | None]:
    """Returns (trend_label, ema25, ema99) from 4H candles."""
    df = df.copy()
    df["ema25"] = df["close"].ewm(span=25, adjust=False).mean()
    df["ema99"] = df["close"].ewm(span=99, adjust=False).mean()
    last   = df.iloc[-1]
    ema25  = safe_float(last["ema25"])
    ema99  = safe_float(last["ema99"])

    if ema25 is None or ema99 is None:
        return "neutral", ema25, ema99
    if ema25 > ema99:
        return "bullish", ema25, ema99
    if ema25 < ema99:
        return "bearish", ema25, ema99
    return "neutral", ema25, ema99


def liquidity_sweep(df: pd.DataFrame) -> Optional[str]:
    """Detect liquidity sweep on the most recent 1H candle."""
    if len(df) < 2:
        return None
    prev = df.iloc[-2]
    last = df.iloc[-1]
    prev_high = safe_float(prev["high"])
    prev_low  = safe_float(prev["low"])
    last_high = safe_float(last["high"])
    last_low  = safe_float(last["low"])
    last_close = safe_float(last["close"])

    if None in (prev_high, prev_low, last_high, last_low, last_close):
        return None

    # Swept below previous low then recovered → bullish sweep (smart money took lows)
    if last_low < prev_low and last_close > prev_low:
        return "bullish_sweep"

    # Swept above previous high then reversed → bearish sweep (smart money took highs)
    if last_high > prev_high and last_close < prev_high:
        return "bearish_sweep"

    return None


def entry_confirmation(df: pd.DataFrame, trend: str, sweep: Optional[str]) -> Optional[str]:
    """15M engulfing candle in direction of trend + sweep."""
    if sweep is None:
        return None
    last  = df.iloc[-1]
    open_ = safe_float(last["open"])
    close = safe_float(last["close"])
    if open_ is None or close is None:
        return None

    if trend == "bullish" and sweep == "bullish_sweep" and close > open_:
        return "LONG"
    if trend == "bearish" and sweep == "bearish_sweep" and close < open_:
        return "SHORT"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

async def run_smc_strategy(symbol: str) -> Optional[dict]:
    """Run the full SMC multi-timeframe analysis for *symbol*."""
    # Fetch all three timeframes in parallel-ish (sequential but fast from Redis/REST)
    candles_4h, candles_1h, candles_15m = await get_ohlcv_multi(
        symbol,
        intervals=["4h", "1h", "15m"],
        limits=[200, 100, 60],
    )

    if len(candles_4h) < 30 or len(candles_1h) < 10 or len(candles_15m) < 5:
        logger.warning("%s: insufficient candles for SMC", symbol)
        return None

    df_4h  = build_dataframe(candles_4h)
    df_1h  = build_dataframe(candles_1h)
    df_15m = build_dataframe(candles_15m)

    trend, ema25, ema99 = trend_4h(df_4h)
    sweep  = liquidity_sweep(df_1h)
    entry  = entry_confirmation(df_15m, trend, sweep)

    signal = entry if entry in ("LONG", "SHORT") else "WAIT"

    price = safe_float(df_15m.iloc[-1]["close"])

    return {
        "symbol":       symbol,
        "signal":       signal,
        "trend":        f"4H {trend}",
        "sweep":        f"1H {sweep}" if sweep else "1H none",
        "entry":        f"15M {entry}" if entry else "15M none",
        "price":        price,
        "tf_4h_ema25":  ema25,
        "tf_4h_ema99":  ema99,
    }
