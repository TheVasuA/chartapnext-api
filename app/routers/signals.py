"""
REST endpoints for signals.

GET  /signals/           — latest cached signal for every tracked symbol
GET  /signals/{symbol}   — latest cached signal for one symbol
GET  /signals/{symbol}/history?limit=50  — historical records from PostgreSQL
"""

import json
from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.db_models import Signal
from app.models.schemas import SignalOut
from app.redis_client import get_redis
from app.utils.symbols import SYMBOLS

router = APIRouter()


@router.get("/", response_model=List[dict])
async def list_signals(redis=Depends(get_redis)):
    """Return the most-recent cached signal for every symbol in one call."""
    pipe = redis.pipeline()
    for sym in SYMBOLS:
        pipe.get(f"signal:{sym}")
    results = await pipe.execute()
    return [json.loads(r) for r in results if r]


@router.get("/{symbol}", response_model=dict)
async def get_signal(symbol: str, redis=Depends(get_redis)):
    """Return the latest signal for a single symbol."""
    raw = await redis.get(f"signal:{symbol.upper()}")
    if raw:
        return json.loads(raw)
    return {"symbol": symbol.upper(), "signal": "HOLD", "price": None}


@router.get("/{symbol}/history", response_model=List[SignalOut])
async def signal_history(
    symbol: str,
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Return the last *limit* signals for *symbol* from PostgreSQL."""
    stmt = (
        select(Signal)
        .where(Signal.symbol == symbol.upper())
        .order_by(desc(Signal.created_at))
        .limit(limit)
    )
    result = await db.execute(stmt)
    return result.scalars().all()
