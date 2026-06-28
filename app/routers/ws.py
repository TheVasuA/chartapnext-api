"""
WebSocket endpoints — push live signals to the Next.js frontend.

/ws/signals           — receive signals for ALL symbols  (Pro only)
/ws/signals/{symbol}  — receive signals for ONE symbol   (Pro only)

A single in-process broadcaster (`signal_broadcaster.broadcaster`) keeps
exactly one Redis pubsub subscription open and fans messages out to
every connected client via per-client asyncio queues. This keeps Redis
load constant regardless of concurrent WS user count.
"""

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.auth import ws_require_plan
from app.services.signal_broadcaster import broadcaster, parse

router = APIRouter()
logger = logging.getLogger(__name__)


async def _stream(websocket: WebSocket, symbol: str | None):
    queue = await broadcaster.subscribe()
    try:
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                # Heartbeat keeps proxies (nginx, ingress) from idle-killing the conn
                try:
                    await websocket.send_text('{"type":"ping"}')
                except Exception:
                    break
                continue

            if symbol:
                data = parse(payload)
                if not data or data.get("symbol") != symbol:
                    continue
            try:
                await websocket.send_text(payload)
            except Exception:
                break
    finally:
        await broadcaster.unsubscribe(queue)


@router.websocket("/ws/signals")
async def ws_all_signals(websocket: WebSocket):
    try:
        principal = await ws_require_plan(websocket, "pro")
    except Exception:
        return
    await websocket.accept()
    logger.info("WS connected (all) user=%s", principal.id)
    try:
        await _stream(websocket, None)
    except WebSocketDisconnect:
        pass
    finally:
        logger.info("WS disconnected (all) user=%s", principal.id)


@router.websocket("/ws/signals/{symbol}")
async def ws_symbol_signals(websocket: WebSocket, symbol: str):
    try:
        principal = await ws_require_plan(websocket, "pro")
    except Exception:
        return
    await websocket.accept()
    sym = symbol.upper()
    logger.info("WS connected (%s) user=%s", sym, principal.id)
    try:
        await _stream(websocket, sym)
    except WebSocketDisconnect:
        pass
    finally:
        logger.info("WS disconnected (%s) user=%s", sym, principal.id)
