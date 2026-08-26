"""
Consolidation Breakout Router
──────────────────────────────
Dedicated endpoint for consolidation breakout signals with:
  - GET /signals          → latest scan results (cached)
  - GET /signals/long     → BUY signals only
  - GET /signals/short    → SELL signals only
  - POST /telegram/subscribe   → subscribe a Telegram chat ID for alerts
  - DELETE /telegram/unsubscribe → unsubscribe a Telegram chat ID
  - GET /telegram/subscribers   → list subscribed chat IDs (admin)
  - POST /telegram/test         → send a test message to verify setup
"""

import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.redis_client import get_redis
from app.services.auth import get_principal_optional, PLAN_RANK
from app.services.scalping_strategy import run_scalping_scan
from app.services.telegram_service import send_telegram_message

router = APIRouter()
logger = logging.getLogger(__name__)

CACHE_KEY = "consolidation:signals"
CACHE_TTL = 300
TELEGRAM_SUBS_KEY = "consolidation:telegram_subscribers"


# ─── Pydantic models ──────────────────────────────────────────────────────────

class TelegramSubscribeRequest(BaseModel):
    chat_id: str
    label: str | None = None  # optional friendly name


class TelegramTestRequest(BaseModel):
    chat_id: str


# ─── Signal endpoints ─────────────────────────────────────────────────────────

@router.get("/signals")
async def get_consolidation_signals(principal=Depends(get_principal_optional)):
    """Return the latest consolidation breakout signals (EMA25 consolidation scan)."""
    redis = await get_redis()
    raw = await redis.get(CACHE_KEY)

    if raw:
        data = json.loads(raw)
        data["source"] = "cache"
        return data

    # Cold cache — only Pro can trigger the full scan
    if principal is None or PLAN_RANK.get(principal.plan, 0) < PLAN_RANK["pro"]:
        return {
            "signals": [],
            "scannedCount": 0,
            "timestamp": int(time.time() * 1000),
            "source": "warming",
            "message": "Scanner warming up. Try again in 30s or upgrade to Pro.",
        }

    logger.info("consolidation cache cold — on-demand scan (pro user)")
    try:
        signals = await run_scalping_scan()
    except Exception as exc:
        logger.error("on-demand consolidation scan failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    payload = {
        "signals": signals,
        "scannedCount": 60,
        "timestamp": int(time.time() * 1000),
        "source": "on-demand",
    }
    await redis.set(CACHE_KEY, json.dumps(payload), ex=CACHE_TTL)
    return payload


@router.get("/signals/long")
async def get_consolidation_long(principal=Depends(get_principal_optional)):
    """Return only BUY consolidation breakout signals."""
    redis = await get_redis()
    raw = await redis.get(CACHE_KEY)

    if raw:
        data = json.loads(raw)
        buy_signals = [s for s in data.get("signals", []) if s.get("signal") == "BUY"]
        return {
            "signals": buy_signals,
            "scannedCount": data.get("scannedCount", 0),
            "timestamp": data.get("timestamp"),
            "source": "cache",
        }

    return {"signals": [], "scannedCount": 0, "timestamp": int(time.time() * 1000), "source": "warming"}


@router.get("/signals/short")
async def get_consolidation_short(principal=Depends(get_principal_optional)):
    """Return only SELL consolidation breakout signals."""
    redis = await get_redis()
    raw = await redis.get(CACHE_KEY)

    if raw:
        data = json.loads(raw)
        sell_signals = [s for s in data.get("signals", []) if s.get("signal") == "SELL"]
        return {
            "signals": sell_signals,
            "scannedCount": data.get("scannedCount", 0),
            "timestamp": data.get("timestamp"),
            "source": "cache",
        }

    return {"signals": [], "scannedCount": 0, "timestamp": int(time.time() * 1000), "source": "warming"}


# ─── Telegram subscription endpoints ─────────────────────────────────────────

@router.post("/telegram/subscribe")
async def subscribe_telegram(req: TelegramSubscribeRequest, principal=Depends(get_principal_optional)):
    """Subscribe a Telegram chat ID to receive consolidation breakout alerts."""
    redis = await get_redis()

    # Store subscription as a hash: chat_id -> label/metadata
    meta = json.dumps({
        "label": req.label or req.chat_id,
        "subscribed_at": int(time.time()),
        "user": principal.sub if principal else "anonymous",
    })
    await redis.hset(TELEGRAM_SUBS_KEY, req.chat_id, meta)

    return {
        "status": "subscribed",
        "chat_id": req.chat_id,
        "label": req.label,
        "message": f"Chat {req.chat_id} will receive consolidation breakout alerts.",
    }


@router.delete("/telegram/unsubscribe")
async def unsubscribe_telegram(req: TelegramSubscribeRequest, principal=Depends(get_principal_optional)):
    """Unsubscribe a Telegram chat ID from alerts."""
    redis = await get_redis()
    removed = await redis.hdel(TELEGRAM_SUBS_KEY, req.chat_id)

    if removed:
        return {"status": "unsubscribed", "chat_id": req.chat_id}
    raise HTTPException(status_code=404, detail="Chat ID not found in subscribers")


@router.get("/telegram/subscribers")
async def list_telegram_subscribers(principal=Depends(get_principal_optional)):
    """List all subscribed Telegram chat IDs."""
    redis = await get_redis()
    raw = await redis.hgetall(TELEGRAM_SUBS_KEY)

    subscribers = []
    for chat_id, meta_raw in raw.items():
        try:
            meta = json.loads(meta_raw)
        except Exception:
            meta = {"label": chat_id}
        subscribers.append({
            "chat_id": chat_id if isinstance(chat_id, str) else chat_id.decode(),
            **meta,
        })

    return {"subscribers": subscribers, "count": len(subscribers)}


@router.post("/telegram/test")
async def test_telegram(req: TelegramTestRequest, principal=Depends(get_principal_optional)):
    """Send a test message to verify Telegram bot setup."""
    test_msg = (
        "✅ *Chartap — Consolidation Breakout Alerts*\n\n"
        "Your Telegram is connected! You'll receive alerts when "
        "consolidation breakout signals are detected.\n\n"
        "📊 Scanning 60 symbols × 4 timeframes every 2 minutes."
    )
    ok = await send_telegram_message(req.chat_id, test_msg)
    if ok:
        return {"status": "sent", "message": "Test message sent successfully"}
    raise HTTPException(status_code=502, detail="Failed to send test message. Check bot token and chat ID.")
