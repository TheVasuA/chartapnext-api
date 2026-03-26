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
    """Detect a liquidity sweep in the most recent 1H candles.

    Scans the last 5 candles for any candle that swept the swing high/low of
    the previous 10 candles before it.  The most recent sweep direction wins.
    Tolerance is 0.5 % so near-touches count.
    """
    if len(df) < 12:
        return None

    tolerance = 0.005  # 0.5 %
    last_sweep = None

    # Slide a window: for each of the last 5 candles, check against prior 10
    for i in range(-5, 0):
        candle = df.iloc[i]
        window = df.iloc[i - 10 : i]      # 10 candles before this one
        if len(window) < 5:
            continue

        swing_high = safe_float(window["high"].max())
        swing_low  = safe_float(window["low"].min())
        c_high  = safe_float(candle["high"])
        c_low   = safe_float(candle["low"])
        c_open  = safe_float(candle["open"])
        c_close = safe_float(candle["close"])

        if None in (swing_high, swing_low, c_high, c_low, c_open, c_close):
            continue

        # Bullish sweep: wick below swing low + closed above open (or within tolerance)
        swept_low = c_low <= swing_low * (1 + tolerance)
        # Bearish sweep: wick above swing high + closed below open (or within tolerance)
        swept_high = c_high >= swing_high * (1 - tolerance)

        if swept_low and c_close >= c_open:        # bullish candle swept lows
            last_sweep = "bullish_sweep"
        elif swept_high and c_close <= c_open:     # bearish candle swept highs
            last_sweep = "bearish_sweep"
        elif swept_low and c_close > swing_low:    # recovered above sweep level
            last_sweep = "bullish_sweep"
        elif swept_high and c_close < swing_high:  # reversed below sweep level
            last_sweep = "bearish_sweep"

    return last_sweep


def entry_confirmation(df: pd.DataFrame, trend: str, sweep: Optional[str]) -> Optional[str]:
    """15M entry: last candle closes in direction of trend + sweep.

    Uses the last 3 candles and requires majority (2-of-3) to confirm,
    OR a single strong candle (body > 0.3 % of price).
    """
    if sweep is None:
        return None
    if len(df) < 3:
        return None

    # Count bull/bear candles in last 3
    recent = df.iloc[-3:]
    bull = 0
    bear = 0
    for _, r in recent.iterrows():
        o = safe_float(r["open"])
        c = safe_float(r["close"])
        if o is None or c is None:
            continue
        if c > o:
            bull += 1
        elif c < o:
            bear += 1

    # Also check last candle body strength (> 0.2 % of price)
    last = df.iloc[-1]
    lo = safe_float(last["open"])
    lc = safe_float(last["close"])
    strong = False
    if lo is not None and lc is not None and lo > 0:
        body_pct = abs(lc - lo) / lo
        strong = body_pct >= 0.002   # 0.2 %

    if trend == "bullish" and sweep == "bullish_sweep":
        if bull >= 2 or (strong and lc > lo):
            return "LONG"
    if trend == "bearish" and sweep == "bearish_sweep":
        if bear >= 2 or (strong and lc < lo):
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

    # Signal decision:
    #   LONG  — 4H bullish trend + 1H bullish sweep (15M confirmation is bonus)
    #   SHORT — 4H bearish trend + 1H bearish sweep
    #   WAIT  — trend and sweep don't align, or no sweep detected
    if trend == "bullish" and sweep == "bullish_sweep":
        signal = "LONG"
    elif trend == "bearish" and sweep == "bearish_sweep":
        signal = "SHORT"
    else:
        signal = "WAIT"

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
