"""
Breakout Strategy Engine
─────────────────────────────────────────────────────────────────────────────
Single-timeframe breakout logic adapted for live symbol analysis.

Signal output:
  {
    "symbol": "ETHUSDT",
    "signal": "LONG",         # LONG | SHORT | WAIT
    "price":  2345.6,
    "ema25":  2338.1,
    "ema99":  2290.4,
    "atr14":  31.2,
    "color":  "green",
    "breakout_high": 2340.0,
    "breakout_low":  2310.0,
    "consolidation_bars": 7,
    "timestamp": "2026-03-26T12:00:00+00:00"
  }
"""

from datetime import datetime, timezone
from typing import Optional

from app.services.market_data import get_ohlcv


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    ema_vals = [values[0]]
    for v in values[1:]:
        ema_vals.append(v * k + ema_vals[-1] * (1 - k))
    return ema_vals


def _atr(candles: list[dict], period: int = 14) -> list[float]:
    tr = [abs(float(c["high"]) - float(c["low"])) for c in candles]
    atr_vals: list[float] = []
    for i in range(len(tr)):
        start = max(0, i - period + 1)
        window = tr[start : i + 1]
        atr_vals.append(sum(window) / len(window))
    return atr_vals


async def run_breakout_strategy(symbol: str) -> Optional[dict]:
    """Run breakout strategy for one symbol using recent OHLCV candles."""
    candles = await get_ohlcv(symbol, limit=220)
    if len(candles) < 50:
        return None

    closes = [float(c["close"]) for c in candles]
    ema25 = _ema(closes, 25)
    ema99 = _ema(closes, 99)
    atr_vals = _atr(candles, 14)

    hh2_vals: list[float] = []
    colors: list[str] = []

    for i in range(len(candles)):
        last3 = candles[max(0, i - 2) : i + 1]
        hh2 = sum((float(c["close"]) + float(c["open"])) for c in last3) / (2 * len(last3))
        hh2_vals.append(hh2)

        hh22_window = hh2_vals[max(0, i - 4) : i + 1]
        out_d2 = sum(hh22_window) / len(hh22_window)

        if hh2 > out_d2:
            colors.append("green")
        elif hh2 < out_d2:
            colors.append("red")
        else:
            colors.append("gray")

    consolidation_min = 6
    consolidation_count = 0
    in_consolidation = False

    last_signal = "WAIT"
    signal_idx: int | None = None
    breakout_high: float | None = None
    breakout_low: float | None = None
    signal_color = colors[-1] if colors else "gray"

    for i in range(20, len(candles)):
        price = float(candles[i]["close"])
        near_ema = abs(price - ema25[i]) < (atr_vals[i] * 0.5)

        if colors[i] == "gray" or near_ema:
            consolidation_count += 1
            in_consolidation = True
            continue

        if in_consolidation and consolidation_count >= consolidation_min:
            trend_up = ema25[i] > ema99[i]
            trend_down = ema25[i] < ema99[i]

            prev_slice = candles[i - 5 : i]
            prev_high = max(float(c["high"]) for c in prev_slice)
            prev_low = min(float(c["low"]) for c in prev_slice)

            strong_bull = float(candles[i]["close"]) > prev_high
            strong_bear = float(candles[i]["close"]) < prev_low

            prev_lows_10 = [float(c["low"]) for c in candles[i - 10 : i]]
            prev_highs_10 = [float(c["high"]) for c in candles[i - 10 : i]]

            sweep_low = float(candles[i]["low"]) < min(prev_lows_10)
            sweep_high = float(candles[i]["high"]) > max(prev_highs_10)

            atr_window = atr_vals[i - 20 : i]
            avg_atr = sum(atr_window) / len(atr_window)
            volatility = atr_vals[i] > avg_atr

            if trend_up and strong_bull and sweep_low and volatility and colors[i] == "green":
                if signal_idx is None or signal_idx != i - 1:
                    last_signal = "LONG"
                    signal_idx = i
                    breakout_high = prev_high
                    breakout_low = prev_low
                    signal_color = colors[i]
                    consolidation_count = 0
                    in_consolidation = False
                    continue

            if trend_down and strong_bear and sweep_high and volatility and colors[i] == "red":
                if signal_idx is None or signal_idx != i - 1:
                    last_signal = "SHORT"
                    signal_idx = i
                    breakout_high = prev_high
                    breakout_low = prev_low
                    signal_color = colors[i]
                    consolidation_count = 0
                    in_consolidation = False
                    continue

        consolidation_count = 0
        in_consolidation = False

    ts_idx = signal_idx if signal_idx is not None else len(candles) - 1
    ts = datetime.fromtimestamp(int(candles[ts_idx]["open_time"]) / 1000, tz=timezone.utc).isoformat()

    return {
        "symbol": symbol,
        "signal": last_signal,
        "price": float(candles[-1]["close"]),
        "ema25": round(ema25[-1], 6) if ema25 else None,
        "ema99": round(ema99[-1], 6) if ema99 else None,
        "atr14": round(atr_vals[-1], 6) if atr_vals else None,
        "color": signal_color,
        "breakout_high": round(breakout_high, 6) if breakout_high is not None else None,
        "breakout_low": round(breakout_low, 6) if breakout_low is not None else None,
        "consolidation_bars": consolidation_count,
        "timestamp": ts,
    }
