import json
import logging
from typing import List

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.redis_client import get_redis
from app.services.breakout_strategy import run_breakout_strategy
from app.utils.symbols import SYMBOLS

router = APIRouter()
logger = logging.getLogger(__name__)

BREAKOUT_KEY = "breakout:{symbol}"
BREAKOUT_TTL = 300


async def _list_or_compute_breakout() -> list[dict]:
    """Return cached breakout analyses, computing and caching on first miss."""
    redis = await get_redis()
    pipe = redis.pipeline()
    for sym in SYMBOLS:
        pipe.get(BREAKOUT_KEY.format(symbol=sym))
    raw = await pipe.execute()

    cached = [json.loads(r) for r in raw if r]
    if cached:
        return cached

    # Cache is cold: compute once so frontend doesn't get an empty list forever.
    computed: list[dict] = []
    for sym in SYMBOLS:
        try:
            result = await run_breakout_strategy(sym)
            if result:
                computed.append(result)
        except Exception as exc:
            logger.warning("Breakout compute failed for %s: %s", sym, exc)

    if computed:
        write_pipe = redis.pipeline()
        for row in computed:
            write_pipe.set(
                BREAKOUT_KEY.format(symbol=row["symbol"]),
                json.dumps(row),
                ex=BREAKOUT_TTL,
            )
        await write_pipe.execute()

    return computed

class Candle(BaseModel):
    t: int  # timestamp
    o: float  # open
    h: float  # high
    l: float  # low
    c: float  # close

class Signal(BaseModel):
    index: int
    type: str  # 'buy' or 'sell'
    timestamp: int


@router.get("/", response_model=List[dict])
async def list_breakout():
    """Return latest cached breakout analysis for all symbols."""
    return await _list_or_compute_breakout()


@router.get("/signals/long", response_model=List[dict])
async def breakout_long_signals():
    """Return only LONG breakout signals."""
    rows = await _list_or_compute_breakout()
    return [r for r in rows if r.get("signal") == "LONG"]


@router.get("/signals/short", response_model=List[dict])
async def breakout_short_signals():
    """Return only SHORT breakout signals."""
    rows = await _list_or_compute_breakout()
    return [r for r in rows if r.get("signal") == "SHORT"]


@router.get("/symbol/{symbol}", response_model=dict)
async def get_breakout(symbol: str):
    """Compute and cache breakout analysis for a single symbol."""
    sym = symbol.upper()
    redis = await get_redis()

    cached = await redis.get(BREAKOUT_KEY.format(symbol=sym))
    if cached:
        return json.loads(cached)

    result = await run_breakout_strategy(sym)
    if not result:
        raise HTTPException(status_code=503, detail="Insufficient data for breakout analysis")

    await redis.set(BREAKOUT_KEY.format(symbol=sym), json.dumps(result), ex=BREAKOUT_TTL)
    return result

def ema(values: List[float], period: int) -> List[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    ema_vals = [values[0]]
    for v in values[1:]:
        ema_vals.append(v * k + ema_vals[-1] * (1 - k))
    return ema_vals

@router.post("/breakout-signals", response_model=List[Signal])
def breakout_signals(candles: List[Candle]):
    hh2_vals = []
    signals = []
    processed = []
    for i, candle in enumerate(candles):
        last3 = candles[max(0, i-2):i+1]
        last3_sum = [c.c + c.o for c in last3]
        hh2 = np.mean(last3_sum) / 2
        hh2_vals.append(hh2)
        hh22 = np.mean(hh2_vals[-5:]) if len(hh2_vals) >= 5 else np.mean(hh2_vals)
        outD2 = np.mean([hh22]*5)
        if hh2 > outD2:
            color = 'green'
        elif hh2 < outD2:
            color = 'red'
        else:
            color = 'gray'
        processed.append({
            't': candle.t,
            'o': candle.o,
            'c': candle.c,
            'color': color
        })
    closes = [c['c'] for c in processed]
    ema25 = ema(closes, 25)
    for i in range(1, len(processed)):
        if (
            processed[i]['color'] == 'green' and
            ema25[i-1] < min(processed[i-1]['o'], processed[i-1]['c']) and
            ema25[i] > max(processed[i]['o'], processed[i]['c'])
        ):
            signals.append(Signal(index=i, type='buy', timestamp=processed[i]['t']))
        if (
            processed[i]['color'] == 'red' and
            ema25[i-1] > max(processed[i-1]['o'], processed[i-1]['c']) and
            ema25[i] < min(processed[i]['o'], processed[i]['c'])
        ):
            signals.append(Signal(index=i, type='sell', timestamp=processed[i]['t']))
    return signals
