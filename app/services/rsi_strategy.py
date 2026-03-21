"""
RSI Pullback Strategy Engine
─────────────────────────────────────────────────────────────────────────────
Multi-timeframe analysis:
  • 4H → trend filter  : EMA 100 (price above/below) + RSI 50 threshold
  • 1H → entry timing  : RSI pullback into 40-55 zone then turns back up/down

Signal output:
  {
    "symbol":      "ETHUSDT",
    "signal":      "LONG",        # LONG | SHORT | WAIT
    "price":       2345.6,
    "rsi_4h":      58.2,
    "rsi_1h":      51.4,
    "ema100_4h":   82000.0,
    "trend":       "bullish",
    "pullback":    true,
    "timestamp":   "2026-03-21T12:00:00Z",
  }
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from app.services.indicators import build_dataframe, safe_float
from app.services.market_data import get_ohlcv_multi

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Sub-indicators
# ─────────────────────────────────────────────────────────────────────────────

def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs  = avg_gain / avg_loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def analyze_4h(df: pd.DataFrame) -> tuple[str, float | None, float | None, float | None]:
    """Returns (trend, rsi_4h, ema100, price).

    trend = 'bullish' | 'bearish' | 'neutral'
    """
    df = df.copy()
    df["rsi"]    = _rsi(df["close"], 14)
    df["ema100"] = _ema(df["close"], 100)
    last = df.iloc[-1]

    price  = safe_float(last["close"])
    rsi    = safe_float(last["rsi"])
    ema100 = safe_float(last["ema100"])

    if price is None or rsi is None or ema100 is None:
        return "neutral", rsi, ema100, price

    if price > ema100 and rsi > 50:
        trend = "bullish"
    elif price < ema100 and rsi < 50:
        trend = "bearish"
    else:
        trend = "neutral"

    return trend, rsi, ema100, price


def analyze_1h(df: pd.DataFrame, trend: str) -> tuple[bool, float | None]:
    """Returns (pullback_confirmed, rsi_1h).

    Pullback logic:
      LONG  — RSI dropped into 38-55 zone in last 5 candles, now turning UP
              (latest RSI > previous RSI, and latest > 45)
      SHORT — RSI rose into 45-62 zone in last 5 candles, now turning DOWN
              (latest RSI < previous RSI, and latest < 55)
    """
    df = df.copy()
    df["rsi"] = _rsi(df["close"], 14)

    if len(df) < 6:
        return False, None

    rsi_series = df["rsi"].dropna()
    if len(rsi_series) < 3:
        return False, None

    cur  = safe_float(rsi_series.iloc[-1])
    prev = safe_float(rsi_series.iloc[-2])

    if cur is None or prev is None:
        return False, cur

    # Check last 5 candles had a dip (LONG) or spike (SHORT) into the zone
    window_rsi = [safe_float(v) for v in rsi_series.iloc[-6:-1] if safe_float(v) is not None]
    if not window_rsi:
        return False, cur

    if trend == "bullish":
        # At least one candle in last 5 dipped into 38-56 (pullback zone)
        had_pullback = any(38 <= v <= 56 for v in window_rsi)
        # Now turning up: current RSI is rising and above 44
        turning_up   = cur > prev and cur >= 44
        return had_pullback and turning_up, cur

    if trend == "bearish":
        # At least one candle in last 5 rose into 44-62 (pullback zone)
        had_pullback = any(44 <= v <= 62 for v in window_rsi)
        # Now turning down: current RSI is falling and below 56
        turning_down = cur < prev and cur <= 56
        return had_pullback and turning_down, cur

    return False, cur


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

async def run_rsi_strategy(symbol: str) -> Optional[dict]:
    """Run the RSI pullback strategy for *symbol*."""
    candles_4h, candles_1h = await get_ohlcv_multi(
        symbol,
        intervals=["4h", "1h"],
        limits=[200, 60],
    )

    if len(candles_4h) < 110 or len(candles_1h) < 10:
        logger.warning("%s: insufficient candles for RSI strategy", symbol)
        return None

    df_4h = build_dataframe(candles_4h)
    df_1h = build_dataframe(candles_1h)

    trend, rsi_4h, ema100, price = analyze_4h(df_4h)
    pullback, rsi_1h = analyze_1h(df_1h, trend)

    if trend == "bullish" and pullback:
        signal = "LONG"
    elif trend == "bearish" and pullback:
        signal = "SHORT"
    else:
        signal = "WAIT"

    return {
        "symbol":    symbol,
        "signal":    signal,
        "price":     price,
        "rsi_4h":    round(rsi_4h, 2) if rsi_4h is not None else None,
        "rsi_1h":    round(rsi_1h, 2) if rsi_1h is not None else None,
        "ema100_4h": round(ema100, 4) if ema100 is not None else None,
        "trend":     trend,
        "pullback":  pullback,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
