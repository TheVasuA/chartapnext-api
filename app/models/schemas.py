from datetime import datetime

from pydantic import BaseModel

from app.models.db_models import SignalType


class SignalOut(BaseModel):
    id:          int
    symbol:      str
    signal:      SignalType
    price:       float
    rsi:         float | None = None
    macd:        float | None = None
    macd_signal: float | None = None
    bb_upper:    float | None = None
    bb_lower:    float | None = None
    interval:    str
    created_at:  datetime

    model_config = {"from_attributes": True}


class SignalLive(BaseModel):
    """Shape pushed over WebSocket to the frontend."""
    symbol:      str
    signal:      SignalType
    price:       float | None = None
    rsi:         float | None = None
    macd:        float | None = None
    macd_signal: float | None = None
    bb_upper:    float | None = None
    bb_lower:    float | None = None
    interval:    str
    timestamp:   str
