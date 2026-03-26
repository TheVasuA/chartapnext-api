# Chartapnext API Contract

Base URL (local): `http://localhost:8000`

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
