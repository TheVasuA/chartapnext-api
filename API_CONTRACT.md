# Chartapnext API Contract

Base URL (local): `http://localhost:8005`

## Health

### GET /health
Response:
```json
{
  "status": "ok"
}
```

## Coins

### GET /coins/
Returns tracked symbols.

Response:
```json
[
  { "symbol": "BTCUSDT" },
  { "symbol": "ETHUSDT" }
]
```

## Generic Signals (BUY/SELL/HOLD)

### GET /signals/
Returns latest cached signal for each tracked symbol.

Response item shape:
```json
{
  "id": 123,
  "symbol": "BTCUSDT",
  "signal": "BUY",
  "price": 67890.12,
  "bb_upper": 68400.0,
  "bb_lower": 67100.0,
  "interval": "1m",
  "timestamp": "2026-03-26T15:01:10.123456Z"
}
```

### GET /signals/{symbol}
Returns latest cached signal for a symbol.

If no cache exists:
```json
{
  "symbol": "BTCUSDT",
  "signal": "HOLD",
  "price": null
}
```

### GET /signals/{symbol}/history?limit=50
Returns historical records from PostgreSQL.

Query params:
- `limit` (int, optional, default `50`, min `1`, max `500`)

Response item shape:
```json
{
  "id": 123,
  "symbol": "BTCUSDT",
  "signal": "BUY",
  "price": 67890.12,
  "bb_upper": 68400.0,
  "bb_lower": 67100.0,
  "interval": "1m",
  "created_at": "2026-03-26T15:01:10.123456"
}
```

## SMC Signals (LONG/SHORT/WAIT)

### GET /smc/
Returns latest cached SMC analysis for all symbols.

### GET /smc/{symbol}
Computes on-demand if cache is missing, then caches.

### GET /smc/signals/long
Returns only symbols with `signal = "LONG"`.

### GET /smc/signals/short
Returns only symbols with `signal = "SHORT"`.

SMC response shape:
```json
{
  "symbol": "ETHUSDT",
  "signal": "LONG",
  "trend": "4H bullish",
  "sweep": "1H bullish_sweep",
  "entry": "15M LONG",
  "price": 2345.6,
  "tf_4h_ema25": 2300.123,
  "tf_4h_ema99": 2200.987
}
```

## RSI Pullback Signals (LONG/SHORT/WAIT)

### GET /rsi/
Returns latest cached RSI analysis for all symbols.

### GET /rsi/{symbol}
Computes on-demand if cache is missing, then caches.

### GET /rsi/signals/long
Returns only symbols with `signal = "LONG"`.

### GET /rsi/signals/short
Returns only symbols with `signal = "SHORT"`.

RSI response shape:
```json
{
  "symbol": "ETHUSDT",
  "signal": "LONG",
  "price": 2345.6,
  "rsi_4h": 58.2,
  "rsi_1h": 51.4,
  "ema100_4h": 82000.0,
  "trend": "bullish",
  "pullback": true,
  "timestamp": "2026-03-21T12:00:00+00:00"
}
```

## Breakout

### POST /breakout/breakout-signals
Request body:
```json
[
  { "t": 1711450000000, "o": 65000, "h": 65200, "l": 64850, "c": 65100 }
]
```

Response:
```json
[
  { "index": 25, "type": "buy", "timestamp": 1711451500000 },
  { "index": 40, "type": "sell", "timestamp": 1711452400000 }
]
```

## Chart Patterns (classic formations)

Detected with swing/pivot detection (`scipy.signal.find_peaks`) + geometric
rules, then **re-scored for quality**: volume confirmation, EMA trend
alignment, RSI momentum, ATR-based stops, and a minimum risk:reward filter
(only setups with R:R ≥ 1.5 and confidence ≥ 0.45 are emitted). Patterns:
`double_top`, `double_bottom`, `head_shoulders`,
`inverse_head_shoulders`, `ascending_triangle`, `descending_triangle`,
`symmetrical_triangle`, `rising_wedge`, `falling_wedge`, `bull_flag`,
`bear_flag`, `resistance_break`, `support_break`.

Refreshed every 5 min across `15m`, `1h`, and `4h` by Celery beat. `interval`
query param accepts `15m`, `1h` (default), or `4h`.

### GET /patterns/?interval=1h
Full cached docs (incl. candle `window`) for symbols with detected patterns.

### GET /patterns/signals?interval=1h
Lightweight feed — the single best pattern per symbol, sorted by confidence.
Includes the candle `window` so a snapshot can be drawn client-side.

Response item shape:
```json
{
  "symbol": "NEARUSDT",
  "interval": "1h",
  "timestamp": 1711451500000,
  "pattern": "double_bottom",
  "label": "Double Bottom",
  "direction": "bullish",
  "status": "confirmed",
  "confidence": 0.78,
  "quality": "A",
  "volume_ratio": 1.8,
  "volume_confirmed": true,
  "trend": "up",
  "rsi": 58.2,
  "atr": 0.031,
  "reasons": ["Volume 1.8× avg", "Aligned with uptrend"],
  "price": 2.41,
  "entry": 2.45,
  "target": 2.71,
  "stop": 2.30,
  "risk_reward": 2.36,
  "markers": [{ "i": 12, "price": 2.30, "kind": "trough", "label": "Bottom 1" }],
  "lines":   [{ "from": [12, 2.45], "to": [89, 2.45], "kind": "neckline", "label": "Neckline" }],
  "levels":  [{ "price": 2.45, "kind": "entry", "label": "Entry (break)" }],
  "window":  { "interval": "1h", "candles": [{ "t": 1711450000000, "o": 2.4, "h": 2.42, "l": 2.39, "c": 2.41 }] }
}
```
`markers[].i` and `lines[].from[0]` are positions into `window.candles`.
`direction` ∈ `bullish | bearish | neutral`; `status` ∈ `forming | confirmed`.

### GET /patterns/{symbol}?interval=1h
On-demand recompute for one symbol (**Basic+**). Returns `{ symbol, interval,
timestamp, patterns: [...], window }`. `503` if insufficient data.

## Multi-Timeframe Confluence (RSI + MA + MACD)

Top-down confluence across 4H (bias) / 1H (setup) / 15M (trigger). Each
timeframe scores MA / MACD / RSI; combined into a 0–100 confidence
(4H 40% / 1H 35% / 15M 25%), gated by ADX and an RSI-exhaustion guard,
with ATR-based SL/TP. Refreshed every 3 min; cached `mtf:{symbol}`.

### GET /mtf/
Latest cached confluence for all symbols.

### GET /mtf/signals/long  ·  GET /mtf/signals/short
Only LONG / SHORT signals, sorted by confidence.

Response item shape:
```json
{
  "symbol": "BCHUSDT",
  "signal": "LONG",
  "confidence": 79.2,
  "price": 512.3,
  "entry": 512.3,
  "target": 524.1,
  "stop": 506.4,
  "risk_reward": 2.0,
  "atr_1h": 3.9,
  "adx_4h": 27.1,
  "adx_1h": 22.4,
  "timeframes": [
    { "tf": "4h",  "bias": "bull", "ma": "bull", "macd": "bull", "rsi": 61.2, "rsi_state": "bull", "adx": 27.1 },
    { "tf": "1h",  "bias": "bull", "ma": "bull", "macd": "bull", "rsi": 58.0, "rsi_state": "bull", "adx": 22.4 },
    { "tf": "15m", "bias": "bull", "ma": "bull", "macd": "bull", "rsi": 55.4, "rsi_state": "bull", "adx": 19.0 }
  ],
  "timestamp": "2026-06-29T10:00:00+00:00"
}
```
`signal` ∈ `LONG | SHORT | WAIT`; each timeframe's `ma/macd/rsi_state` ∈ `bull | bear | neutral`.

### GET /mtf/{symbol}
On-demand recompute for one symbol (**Basic+**). `503` if insufficient data.

## Swing Confluence (Dynamic Trend Matrix · 4H + 15M)

Ports the "Uptrick: Dynamic Trend Matrix" trend engine (fast/base/slow EMA
stack + slope + ATR-band trail) to two timeframes. A signal fires **only when
both agree**: `LONG` (Buy) when 4H trend up AND 15M trigger up; `SHORT` (Sell)
when both down; else `WAIT`. Risk/TP levels come from the 15M trail (1×/2×/3×
risk). Refreshed every 3 min; cached `swing:{symbol}`.

### GET /swing/
Latest cached swing analysis for all symbols.

### GET /swing/signals/long  ·  GET /swing/signals/short
Only LONG / SHORT signals, sorted by confidence.

Response item shape:
```json
{
  "symbol": "BTCUSDT",
  "signal": "LONG",
  "price": 67890.12,
  "confidence": 72.4,
  "entry": 67890.12,
  "stop": 66950.0,
  "tp1": 68830.0,
  "tp2": 69770.0,
  "tp3": 70710.0,
  "risk_reward": 2.0,
  "bias_4h": "up",
  "trigger_15m": "up",
  "strength_4h": 0.61,
  "strength_15m": 0.54,
  "confirmed": true,
  "trail_15m": 66950.0,
  "timestamp": "2026-06-29T10:00:00+00:00"
}
```
`signal` ∈ `LONG | SHORT | WAIT`; `bias_4h`/`trigger_15m` ∈ `up | down | flat`.

### GET /swing/{symbol}
On-demand recompute for one symbol (**Basic+**). `503` if insufficient data.

## WebSocket


### WS /ws/signals
Streams all generic signal updates from Redis pub/sub channel `signals`.

Message shape:
```json
{
  "id": 123,
  "symbol": "BTCUSDT",
  "signal": "BUY",
  "price": 67890.12,
  "rsi": 33.5,
  "macd": 12.4,
  "macd_signal": 11.9,
  "bb_upper": 68400.0,
  "bb_lower": 67100.0,
  "interval": "1m",
  "timestamp": "2026-03-26T15:01:10.123456Z"
}
```

### WS /ws/signals/{symbol}
Same payload shape as `/ws/signals`, filtered server-side to requested symbol.

## Status Codes

Common status codes:
- `200`: Successful REST response
- `101`: WebSocket protocol upgrade accepted
- `503`: Insufficient data for on-demand SMC/RSI computation

## Notes For Frontend Integration

- `/signals/*` uses `BUY | SELL | HOLD`.
- `/smc/*` and `/rsi/*` use `LONG | SHORT | WAIT`.
- Symbol casing is uppercase in responses.
- Primary symbol source in backend is `app/utils/symbols.py`.
