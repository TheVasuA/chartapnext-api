"""
WebSocket endpoints — push live signals to the Next.js frontend.

/ws/signals           — receive signals for ALL symbols
/ws/signals/{symbol}  — receive signals for ONE symbol only

Redis pub/sub channel "signals" is the single source of truth.
signal_service.save_and_publish() writes to that channel every time
the strategy engine produces a new signal.
"""

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.redis_client import get_redis

router = APIRouter()
logger = logging.getLogger(__name__)


@router.websocket("/ws/signals")
async def ws_all_signals(websocket: WebSocket):
    """Stream every new signal to the connected client."""
    await websocket.accept()
    redis  = await get_redis()
    pubsub = redis.pubsub()
    await pubsub.subscribe("signals")
    logger.info("WS client connected (all symbols)")
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                await websocket.send_text(message["data"])
    except WebSocketDisconnect:
        logger.info("WS client disconnected (all symbols)")
    except Exception as exc:
        logger.error("WS error: %s", exc)
    finally:
        await pubsub.unsubscribe("signals")
        await pubsub.aclose()


@router.websocket("/ws/signals/{symbol}")
async def ws_symbol_signals(websocket: WebSocket, symbol: str):
    """Stream signals for a single *symbol* to the connected client."""
    await websocket.accept()
    redis  = await get_redis()
    pubsub = redis.pubsub()
    await pubsub.subscribe("signals")
    sym = symbol.upper()
    logger.info("WS client connected (%s)", sym)
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                data = json.loads(message["data"])
                if data.get("symbol") == sym:
                    await websocket.send_text(message["data"])
    except WebSocketDisconnect:
        logger.info("WS client disconnected (%s)", sym)
    finally:
        await pubsub.unsubscribe("signals")
        await pubsub.aclose()
