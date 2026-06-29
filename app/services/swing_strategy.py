"""
Swing Strategy — Dynamic Trend Matrix confluence (4H bias + 15M trigger).
─────────────────────────────────────────────────────────────────────────────
Ports the trend engine of the "Uptrick: Dynamic Trend Matrix" indicator
(CC BY-SA 4.0) to Python and applies it on two timeframes:

  • 4H  → directional BIAS
  • 15M → operational TRIGGER

A signal only fires when BOTH timeframes agree:
  LONG  ⇒ 4H trend up   AND 15M trend up
  SHORT ⇒ 4H trend down AND 15M trend down
  else WAIT.

The per-timeframe engine:
  fast EMA(8) / base EMA(21) / slow EMA(55), slope of the base, ATR(10),
  and an ATR-band "trail" state machine that flips long/short when price
  closes beyond the prior band. `bullPressure`/`bearPressure` (full EMA
  stack + slope) confirm the trend. Risk/TP levels are taken from the 15M
  trail, matching the indicator's TP engine (1× / 2× / 3× risk).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
import pandas_ta as ta

from app.services.indicators import build_dataframe, safe_float
from app.services.market_data import get_ohlcv_multi

logger = logging.getLogger(__name__)

# Engine parameters (defaults from the indicator)
FAST_LEN = 8
BASE_LEN = 21
SLOW_LEN = 55
SLOPE_LEN = 5
SMOOTH_LEN = 3
ATR_LEN = 10
ATR_MULT = 2.0

TP1_MULT, TP2_MULT, TP3_MULT = 1.0, 2.0, 3.0


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def compute_trend_matrix(df: pd.DataFrame) -> Optional[dict]:
    """Run the Dynamic Trend Matrix engine on one timeframe.

    Returns the last-bar state: trend (1/-1/0), trail, pressures, strength,
    price, and the raw ATR bands needed for risk/TP sizing.
    """
    if df is None or len(df) < SLOW_LEN + SLOPE_LEN + 5:
        return None

    close = df["close"]
    fast = ta.ema(close, length=FAST_LEN)
    base = ta.ema(close, length=BASE_LEN)
    slow = ta.ema(close, length=SLOW_LEN)
    atr = ta.atr(df["high"], df["low"], close, length=ATR_LEN)
    if fast is None or base is None or slow is None or atr is None:
        return None

    spread = fast - slow
    spread_smooth = ta.ema(spread, length=SMOOTH_LEN)
    slope_raw = base - base.shift(SLOPE_LEN)
    slope_smooth = ta.ema(slope_raw, length=SMOOTH_LEN)

    upper_raw = base + atr * ATR_MULT
    lower_raw = base - atr * ATR_MULT

    closes = close.to_numpy(dtype=float)
    base_np = base.to_numpy(dtype=float)
    upper_np = upper_raw.to_numpy(dtype=float)
    lower_np = lower_raw.to_numpy(dtype=float)

    n = len(closes)
    trend = 0
    trail = float("nan")
    for i in range(n):
        if i == 0 or np.isnan(upper_np[i - 1]) or np.isnan(lower_np[i - 1]):
            if not np.isnan(base_np[i]):
                trail = base_np[i]
            continue

        long_flip = closes[i] > upper_np[i - 1] and trend != 1
        short_flip = closes[i] < lower_np[i - 1] and trend != -1

        if long_flip:
            trend = 1
            trail = lower_np[i]
        elif short_flip:
            trend = -1
            trail = upper_np[i]
        elif trend == 1:
            base_trail = trail if not np.isnan(trail) else lower_np[i]
            trail = max(base_trail, lower_np[i])
        elif trend == -1:
            base_trail = trail if not np.isnan(trail) else upper_np[i]
            trail = min(base_trail, upper_np[i])
        else:
            trail = base_np[i]

    f = safe_float(fast.iloc[-1])
    b = safe_float(base.iloc[-1])
    s = safe_float(slow.iloc[-1])
    slope_v = safe_float(slope_smooth.iloc[-1])
    atr_v = safe_float(atr.iloc[-1])
    spread_v = safe_float(spread_smooth.iloc[-1])
    price = safe_float(close.iloc[-1])

    if None in (f, b, s, slope_v, atr_v, price):
        return None

    bull_pressure = f > b and b > s and slope_v > 0
    bear_pressure = f < b and b < s and slope_v < 0

    strength = 0.0
    if atr_v and atr_v != 0 and spread_v is not None:
        strength = _clamp(abs(spread_v) / atr_v, 0.0, 3.0) / 3.0

    return {
        "trend": trend,                       # 1 long, -1 short, 0 neutral
        "trail": round(trail, 8) if not np.isnan(trail) else None,
        "bull_pressure": bull_pressure,
        "bear_pressure": bear_pressure,
        "strength": round(strength, 3),
        "price": price,
        "upper_raw": safe_float(upper_raw.iloc[-1]),
        "lower_raw": safe_float(lower_raw.iloc[-1]),
        "confirmed": (trend == 1 and bull_pressure) or (trend == -1 and bear_pressure),
    }


async def run_swing_strategy(symbol: str) -> Optional[dict]:
    """4H bias + 15M trigger confluence. Returns LONG/SHORT/WAIT with levels."""
    c4h, c15 = await get_ohlcv_multi(
        symbol,
        intervals=["4h", "15m"],
        limits=[250, 300],
    )

    m4 = compute_trend_matrix(build_dataframe(c4h)) if c4h else None
    m15 = compute_trend_matrix(build_dataframe(c15)) if c15 else None
    if not (m4 and m15):
        logger.warning("%s: insufficient data for swing strategy", symbol)
        return None

    t4, t15 = m4["trend"], m15["trend"]
    price = m15["price"]
    trail15 = m15["trail"]

    # Confluence: both timeframes must agree AND price must be on the correct
    # side of the 15M trail (the stop line). Otherwise it's a borderline /
    # about-to-flip bar that would produce an inverted stop — skip it.
    valid_long = (
        t4 == 1 and t15 == 1
        and (trail15 is None or price is None or price > trail15)
    )
    valid_short = (
        t4 == -1 and t15 == -1
        and (trail15 is None or price is None or price < trail15)
    )
    if valid_long:
        signal = "LONG"
    elif valid_short:
        signal = "SHORT"
    else:
        signal = "WAIT"

    entry = stop = tp1 = tp2 = tp3 = risk = None
    if signal in ("LONG", "SHORT") and price is not None:
        entry = price
        if signal == "LONG":
            floor = m15["lower_raw"] if m15["lower_raw"] is not None else price * 0.99
            risk = max(price - floor, price * 1e-4)
            stop = m15["trail"] if m15["trail"] is not None else floor
            tp1 = entry + risk * TP1_MULT
            tp2 = entry + risk * TP2_MULT
            tp3 = entry + risk * TP3_MULT
        else:
            cap = m15["upper_raw"] if m15["upper_raw"] is not None else price * 1.01
            risk = max(cap - price, price * 1e-4)
            stop = m15["trail"] if m15["trail"] is not None else cap
            tp1 = entry - risk * TP1_MULT
            tp2 = entry - risk * TP2_MULT
            tp3 = entry - risk * TP3_MULT

    rr = None
    if entry and stop and tp2 and abs(entry - stop) > 1e-12:
        rr = round(abs(tp2 - entry) / abs(entry - stop), 2)

    # Confidence: blend of both-TF strength + confirmation bonus.
    confidence = round(
        100.0 * (0.5 * m4["strength"] + 0.5 * m15["strength"])
        + (10.0 if (m4["confirmed"] and m15["confirmed"]) else 0.0),
        1,
    ) if signal != "WAIT" else 0.0
    confidence = min(confidence, 100.0)

    def _dir(t: int) -> str:
        return "up" if t == 1 else "down" if t == -1 else "flat"

    return {
        "symbol": symbol,
        "signal": signal,                       # LONG | SHORT | WAIT
        "price": price,
        "confidence": confidence,
        "entry": round(entry, 8) if entry else None,
        "stop": round(stop, 8) if stop else None,
        "tp1": round(tp1, 8) if tp1 else None,
        "tp2": round(tp2, 8) if tp2 else None,
        "tp3": round(tp3, 8) if tp3 else None,
        "risk_reward": rr,
        "bias_4h": _dir(t4),
        "trigger_15m": _dir(t15),
        "strength_4h": m4["strength"],
        "strength_15m": m15["strength"],
        "confirmed": bool(m4["confirmed"] and m15["confirmed"]),
        "trail_15m": m15["trail"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
