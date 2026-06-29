"""
Multi-Timeframe Triple-Confluence Strategy (RSI + MA + MACD).
─────────────────────────────────────────────────────────────────────────────
Top-down confluence across 4H / 1H / 15M:

  • 4H  = directional BIAS   (EMA stack + MACD vs zero + RSI vs 50)
  • 1H  = SETUP confirmation  (must agree with the 4H bias)
  • 15M = entry TRIGGER       (fresh MACD cross / RSI reclaim of 50 /
                               price reclaim of EMA20, not exhausted)

Each timeframe scores three components — MA (trend), MACD (momentum),
RSI (timing) — as +1 / 0 / -1. We combine them into a 0–100 confidence
score weighted 4H 40% / 1H 35% / 15M 25%, gated by ADX (anti-chop) and an
RSI-exhaustion guard. ATR(14) drives the suggested SL/TP.

Output is designed for a per-symbol "confluence table" UI: each timeframe
reports its MA/MACD/RSI state so the user sees *why* a signal fired.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import pandas_ta as ta

from app.services.indicators import build_dataframe, safe_float
from app.services.market_data import get_ohlcv_multi

logger = logging.getLogger(__name__)

# Confidence weights per timeframe (must sum to 1.0)
W_4H, W_1H, W_15M = 0.40, 0.35, 0.25

ADX_MIN = 20.0          # below this = ranging/chop → no directional signal
RSI_OB, RSI_OS = 72.0, 28.0   # exhaustion guard on the 15m trigger
SIGNAL_MIN_CONFIDENCE = 60.0  # below this we report WAIT


# ─────────────────────────────────────────────────────────────────────────────
# Per-timeframe analysis
# ─────────────────────────────────────────────────────────────────────────────

def _label(score: int) -> str:
    return "bull" if score > 0 else "bear" if score < 0 else "neutral"


def analyze_tf(df: pd.DataFrame) -> Optional[dict]:
    """Score MA / MACD / RSI for one timeframe. Returns a dict or None."""
    if df is None or len(df) < 60:
        return None

    df = df.copy()
    close = df["close"]

    ema20 = ta.ema(close, length=20)
    ema50 = ta.ema(close, length=50)
    ema200 = ta.ema(close, length=200)
    rsi = ta.rsi(close, length=14)
    macd = ta.macd(close, fast=12, slow=26, signal=9)
    atr = ta.atr(df["high"], df["low"], close, length=14)
    adx_df = ta.adx(df["high"], df["low"], close, length=14)

    last = -1
    prev = -2

    price = safe_float(close.iloc[last])
    e20 = safe_float(ema20.iloc[last]) if ema20 is not None else None
    e50 = safe_float(ema50.iloc[last]) if ema50 is not None else None
    e200 = safe_float(ema200.iloc[last]) if ema200 is not None else None
    rsi_v = safe_float(rsi.iloc[last]) if rsi is not None else None
    rsi_prev = safe_float(rsi.iloc[prev]) if rsi is not None and len(rsi) > 1 else None

    macd_v = macds = macdh = macd_prev = macds_prev = None
    if macd is not None and not macd.empty:
        mcol = next((c for c in macd.columns if c.startswith("MACD_")), None)
        scol = next((c for c in macd.columns if c.startswith("MACDs_")), None)
        hcol = next((c for c in macd.columns if c.startswith("MACDh_")), None)
        if mcol and scol:
            macd_v = safe_float(macd[mcol].iloc[last])
            macds = safe_float(macd[scol].iloc[last])
            macd_prev = safe_float(macd[mcol].iloc[prev]) if len(macd) > 1 else None
            macds_prev = safe_float(macd[scol].iloc[prev]) if len(macd) > 1 else None
        if hcol:
            macdh = safe_float(macd[hcol].iloc[last])

    atr_v = safe_float(atr.iloc[last]) if atr is not None else None
    adx_v = None
    if adx_df is not None and not adx_df.empty:
        acol = next((c for c in adx_df.columns if c.startswith("ADX")), None)
        if acol:
            adx_v = safe_float(adx_df[acol].iloc[last])

    # ── MA component: price vs EMA50 vs EMA200 stack ──
    ma_score = 0
    if price is not None and e50 is not None:
        ref = e200 if e200 is not None else e50
        if price > e50 and e50 >= ref:
            ma_score = 1
        elif price < e50 and e50 <= ref:
            ma_score = -1

    # ── MACD component: line vs signal, reinforced by zero-line regime ──
    macd_score = 0
    if macd_v is not None and macds is not None:
        if macd_v > macds:
            macd_score = 1
        elif macd_v < macds:
            macd_score = -1

    # ── RSI component: momentum bias around 50 (small neutral band) ──
    rsi_score = 0
    if rsi_v is not None:
        if rsi_v >= 52:
            rsi_score = 1
        elif rsi_v <= 48:
            rsi_score = -1

    total = ma_score + macd_score + rsi_score   # -3 .. +3
    # Normalize to a bullish strength in [0, 1]
    bull_strength = (total + 3) / 6.0

    # Fresh-cross flags (for the 15m trigger)
    macd_cross_up = (
        macd_v is not None and macds is not None
        and macd_prev is not None and macds_prev is not None
        and macd_prev <= macds_prev and macd_v > macds
    )
    macd_cross_down = (
        macd_v is not None and macds is not None
        and macd_prev is not None and macds_prev is not None
        and macd_prev >= macds_prev and macd_v < macds
    )
    rsi_cross_up = rsi_v is not None and rsi_prev is not None and rsi_prev <= 50 < rsi_v
    rsi_cross_down = rsi_v is not None and rsi_prev is not None and rsi_prev >= 50 > rsi_v

    return {
        "bias": _label(total),
        "score": total,
        "bull_strength": bull_strength,
        "price": price,
        "rsi": round(rsi_v, 2) if rsi_v is not None else None,
        "adx": round(adx_v, 2) if adx_v is not None else None,
        "atr": atr_v,
        "ma": _label(ma_score),
        "macd": _label(macd_score),
        "rsi_state": _label(rsi_score),
        "_macd_cross_up": macd_cross_up,
        "_macd_cross_down": macd_cross_down,
        "_rsi_cross_up": rsi_cross_up,
        "_rsi_cross_down": rsi_cross_down,
        "_price_above_ema20": (price is not None and e20 is not None and price > e20),
    }


def _tf_public(tf_name: str, a: dict) -> dict:
    """Strip internal flags for the frontend confluence table."""
    return {
        "tf": tf_name,
        "bias": a["bias"],
        "ma": a["ma"],
        "macd": a["macd"],
        "rsi": a["rsi"],
        "rsi_state": a["rsi_state"],
        "adx": a["adx"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

async def run_mtf_strategy(symbol: str) -> Optional[dict]:
    """Run the 4H/1H/15M triple-confluence analysis for *symbol*."""
    c4h, c1h, c15 = await get_ohlcv_multi(
        symbol,
        intervals=["4h", "1h", "15m"],
        limits=[250, 250, 300],
    )

    a4 = analyze_tf(build_dataframe(c4h)) if c4h else None
    a1 = analyze_tf(build_dataframe(c1h)) if c1h else None
    a15 = analyze_tf(build_dataframe(c15)) if c15 else None
    if not (a4 and a1 and a15):
        logger.warning("%s: insufficient data for MTF", symbol)
        return None

    price = a15["price"]
    atr = a1["atr"] or a15["atr"]

    # Confidence (directional)
    long_conf = 100.0 * (
        W_4H * a4["bull_strength"] + W_1H * a1["bull_strength"] + W_15M * a15["bull_strength"]
    )
    short_conf = 100.0 - long_conf

    # 15m trigger
    long_trigger = (
        a15["_macd_cross_up"] or a15["_rsi_cross_up"] or a15["_price_above_ema20"]
    )
    short_trigger = (
        a15["_macd_cross_down"] or a15["_rsi_cross_down"] or not a15["_price_above_ema20"]
    )

    # ADX anti-chop gate (use the strongest of 4h/1h)
    adx_gate = max(a4["adx"] or 0, a1["adx"] or 0) >= ADX_MIN

    # Exhaustion guard on 15m RSI
    long_ok = a15["rsi"] is None or a15["rsi"] < RSI_OB
    short_ok = a15["rsi"] is None or a15["rsi"] > RSI_OS

    signal = "WAIT"
    confidence = max(long_conf, short_conf)

    bull_aligned = a4["bias"] == "bull" and a1["score"] > 0
    bear_aligned = a4["bias"] == "bear" and a1["score"] < 0

    if bull_aligned and long_trigger and long_ok and adx_gate and long_conf >= SIGNAL_MIN_CONFIDENCE:
        signal = "LONG"
        confidence = long_conf
    elif bear_aligned and short_trigger and short_ok and adx_gate and short_conf >= SIGNAL_MIN_CONFIDENCE:
        signal = "SHORT"
        confidence = short_conf

    # ATR-based risk levels (2:1 R:R)
    entry = target = stop = rr = None
    if price is not None and atr and signal in ("LONG", "SHORT"):
        entry = price
        if signal == "LONG":
            stop = price - 1.5 * atr
            target = price + 3.0 * atr
        else:
            stop = price + 1.5 * atr
            target = price - 3.0 * atr
        rr = round(abs(target - entry) / abs(entry - stop), 2) if entry != stop else None

    return {
        "symbol": symbol,
        "signal": signal,
        "confidence": round(confidence, 1),
        "price": price,
        "entry": entry,
        "target": target,
        "stop": stop,
        "risk_reward": rr,
        "atr_1h": atr,
        "adx_4h": a4["adx"],
        "adx_1h": a1["adx"],
        "timeframes": [
            _tf_public("4h", a4),
            _tf_public("1h", a1),
            _tf_public("15m", a15),
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
