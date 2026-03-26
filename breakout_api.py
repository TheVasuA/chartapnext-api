from fastapi import FastAPI, Request
from pydantic import BaseModel
from typing import List, Dict, Any
import numpy as np

app = FastAPI()

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

# --- Helper Functions ---
def ema(values: List[float], period: int) -> List[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    ema_vals = [values[0]]
    for v in values[1:]:
        ema_vals.append(v * k + ema_vals[-1] * (1 - k))
    return ema_vals

@app.post("/breakout-signals", response_model=List[Signal])
def breakout_signals(candles: List[Candle]):
    # Step 1: Compute custom candle logic (hh2, outD2, color)
    hh2_vals = []
    signals = []
    processed = []
    for i, candle in enumerate(candles):
        last3 = candles[max(0, i-2):i+1]
        last3_sum = [c.c + c.o for c in last3]
        hh2 = np.mean(last3_sum) / 2
        hh2_vals.append(hh2)
        # Compute hh22 and outD2
        hh22 = np.mean(hh2_vals[-5:]) if len(hh2_vals) >= 5 else np.mean(hh2_vals)
        outD2 = np.mean([hh22]*5)
        # Color logic
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
    # Step 2: Compute EMA25
    closes = [c['c'] for c in processed]
    ema25 = ema(closes, 25)
    # Step 3: Detect signals (color + EMA25 cross)
    for i in range(1, len(processed)):
        # Bullish breakout
        if (
            processed[i]['color'] == 'green' and
            ema25[i-1] < min(processed[i-1]['o'], processed[i-1]['c']) and
            ema25[i] > max(processed[i]['o'], processed[i]['c'])
        ):
            signals.append(Signal(index=i, type='buy', timestamp=processed[i]['t']))
        # Bearish breakout
        if (
            processed[i]['color'] == 'red' and
            ema25[i-1] > max(processed[i-1]['o'], processed[i-1]['c']) and
            ema25[i] < min(processed[i]['o'], processed[i]['c'])
        ):
            signals.append(Signal(index=i, type='sell', timestamp=processed[i]['t']))
    return signals
