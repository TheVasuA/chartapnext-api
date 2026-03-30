"""
Consolidation Breakout (Scalping) Strategy
───────────────────────────────────────────
Identical logic to the Next.js /api/scalping-signals route:
  - EMA25 of closes
  - hh2  = mean of last-3 (close + open) / 2
  - hh22 = exponential moving average (period=5) of hh2 accumulator
  - color1: green  when hh2 > hh22  (BUY side)
  - color2: red    when hh2 < hh22  (SELL side)

Signal fired on LAST candle ONLY:
  BUY  – color1 == green  AND close > ema25,  preceded by ≥10 gray-color1 candles
  SELL – color2 == red    AND close < ema25,  preceded by ≥10 gray-color2 candles

Runs across four timeframes per symbol: 15m, 1h, 4h, 8h.
"""

import asyncio
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

BINANCE_REST   = "https://api.binance.com/api/v3/klines"
TIMEFRAMES     = ["15m", "1h", "4h", "8h"]
CANDLE_LIMIT   = 200
MIN_CONSOL     = 10   # minimum consecutive gray candles before breakout signal

SCALP_SYMBOLS: list[str] = [
    "1INCHUSDT", "AAVEUSDT",    "ACHUSDT",    "ADAUSDT",    "ALGOUSDT",
    "ATOMUSDT",  "AVAXUSDT",    "AXSUSDT",    "BATUSDT",    "BCHUSDT",
    "BNBUSDT",   "BTCUSDT",     "CELOUSDT",   "CHZUSDT",    "COMPUSDT",
    "DASHUSDT",  "DOGEUSDT",    "DOTUSDT",    "EGLDUSDT",   "ENAUSDT",
    "ENJUSDT",   "ENSUSDT",     "ETCUSDT",    "ETHFIUSDT",  "ETHUSDT",
    "FETUSDT",   "FILUSDT",     "GRTUSDT",    "HBARUSDT",   "INJUSDT",
    "JUPUSDT",   "LINKUSDT",    "LTCUSDT",    "MANAUSDT",   "MASKUSDT",
    "MINAUSDT",  "NEARUSDT",    "NMRUSDT",    "ORDIUSDT",   "PENDLEUSDT",
    "PENGUUSDT", "POLUSDT",     "QTUMUSDT",   "RENDERUSDT", "SANDUSDT",
    "SEIUSDT",   "SOLUSDT",     "STORJUSDT",  "SUIUSDT",    "SUSHIUSDT",
    "THETAUSDT", "TIAUSDT",     "TONUSDT",    "TRBUSDT",    "TRXUSDT",
    "TURBOUSDT", "VIRTUALUSDT", "XRPUSDT",    "ZECUSDT",    "ZROUSDT",
]


# ─── Pure indicator helpers ───────────────────────────────────────────────────

def _ema_scalar(values: list[float], period: int) -> float:
    """EMA scalar (last value) using standard multiplier k=2/(period+1)."""
    if not values:
        return 0.0
    k = 2 / (period + 1)
    v = values[0]
    for x in values[1:]:
        v = x * k + v * (1 - k)
    return v


def _ema_full(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for x in values[1:]:
        out.append(x * k + out[-1] * (1 - k))
    return out


# ─── Colour computation ───────────────────────────────────────────────────────

def _compute_colors(candles: list[dict]) -> list[dict]:
    closes  = [float(c["close"]) for c in candles]
    ema25   = _ema_full(closes, 25)
    hh2_acc: list[float] = []
    out: list[dict] = []

    for i, c in enumerate(candles):
        last3 = candles[max(0, i - 2): i + 1]
        hh2 = sum(float(x["close"]) + float(x["open"]) for x in last3) / (2 * len(last3))
        hh2_acc.append(hh2)
        hh22 = _ema_scalar(hh2_acc[-5:], 5)

        out.append({
            "color1": "green" if hh2 > hh22 else "gray",
            "color2": "red"   if hh2 < hh22 else "gray",
            "ema25":  ema25[i],
            "close":  float(c["close"]),
        })
    return out


# ─── Signal detection ─────────────────────────────────────────────────────────

def _detect_signal(colors: list[dict]) -> Optional[dict]:
    if len(colors) < MIN_CONSOL + 2:
        return None
    last = colors[-1]
    prev = colors[:-1]

    # BUY
    if last["color1"] == "green" and last["close"] > last["ema25"]:
        count = 0
        for row in reversed(prev):
            if row["color1"] == "gray":
                count += 1
            else:
                break
        if count >= MIN_CONSOL:
            ema = last["ema25"]
            cl  = last["close"]
            return {
                "signal":      "BUY",
                "consolCount": count,
                "ema25":       round(ema, 6),
                "close":       round(cl, 6),
                "pctFromEma":  round((cl - ema) / ema * 100, 3),
            }

    # SELL
    if last["color2"] == "red" and last["close"] < last["ema25"]:
        count = 0
        for row in reversed(prev):
            if row["color2"] == "gray":
                count += 1
            else:
                break
        if count >= MIN_CONSOL:
            ema = last["ema25"]
            cl  = last["close"]
            return {
                "signal":      "SELL",
                "consolCount": count,
                "ema25":       round(ema, 6),
                "close":       round(cl, 6),
                "pctFromEma":  round((cl - ema) / ema * 100, 3),
            }

    return None


# ─── Binance kline fetch ──────────────────────────────────────────────────────

async def _fetch_klines(client: httpx.AsyncClient, symbol: str, interval: str) -> list[dict]:
    try:
        resp = await client.get(
            BINANCE_REST,
            params={"symbol": symbol, "interval": interval, "limit": CANDLE_LIMIT},
            timeout=10,
        )
        resp.raise_for_status()
        klines = resp.json()
        return [{"open": k[1], "close": k[4]} for k in klines]
    except Exception as exc:
        logger.warning("kline fetch failed %s %s: %s", symbol, interval, exc)
        return []


# ─── Per-symbol analysis ──────────────────────────────────────────────────────

async def _analyze_symbol(client: httpx.AsyncClient, symbol: str) -> list[dict]:
    tasks   = [_fetch_klines(client, symbol, tf) for tf in TIMEFRAMES]
    results = await asyncio.gather(*tasks)
    hits: list[dict] = []
    for tf, candles in zip(TIMEFRAMES, results):
        if len(candles) < MIN_CONSOL + 2:
            continue
        colors = _compute_colors(candles)
        sig    = _detect_signal(colors)
        if sig:
            hits.append({"symbol": symbol, "timeframe": tf, **sig})
    return hits


# ─── Full scan ────────────────────────────────────────────────────────────────

BATCH_SIZE = 8

async def run_scalping_scan(symbols: list[str] = SCALP_SYMBOLS) -> list[dict]:
    """Scan all symbols and return every signal found."""
    all_signals: list[dict] = []
    async with httpx.AsyncClient() as client:
        for i in range(0, len(symbols), BATCH_SIZE):
            batch   = symbols[i: i + BATCH_SIZE]
            results = await asyncio.gather(*[_analyze_symbol(client, s) for s in batch])
            for hits in results:
                all_signals.extend(hits)

    # BUY first, then SELL; within same type → more consolidation candles first
    all_signals.sort(key=lambda x: (0 if x["signal"] == "BUY" else 1, -x["consolCount"]))
    return all_signals
