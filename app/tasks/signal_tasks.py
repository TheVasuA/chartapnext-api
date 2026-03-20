"""
Celery app + tasks for signal generation.

Tasks
─────
refresh_all_signals  — recompute signals for every symbol (beat: every 60 s)
refresh_signal       — recompute signal for a single symbol (on-demand)

The beat scheduler also wires up a candle-close listener so signals are
recomputed immediately when new candle data arrives (via the Redis
"candle_closed" pub/sub channel), rather than only on the 60-second tick.
"""

import asyncio
import json
import logging

from celery import Celery

from app.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery(
    "chartap",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    task_serializer   = "json",
    result_serializer = "json",
    accept_content    = ["json"],
    timezone          = "UTC",
    enable_utc        = True,
    beat_schedule     = {
        # Fallback full refresh every 60 seconds
        "refresh-all-signals-every-minute": {
            "task":     "app.tasks.signal_tasks.refresh_all_signals",
            "schedule": 60.0,
        },
    },
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _run(coro):
    """Run an async coroutine in a fresh event loop (Celery workers are sync)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─────────────────────────────────────────────────────────────────────────────
# Tasks
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="app.tasks.signal_tasks.refresh_all_signals",
    bind=True,
    max_retries=3,
)
def refresh_all_signals(self):
    """Recompute signals for all tracked symbols and persist + publish results."""
    from app.database import task_session
    from app.services.signal_service import save_and_publish
    from app.services.strategy_engine import run_strategy
    from app.utils.symbols import SYMBOLS

    async def _compute_all():
        async with task_session() as db:
            for symbol in SYMBOLS:
                try:
                    result = await run_strategy(symbol)
                    if result:
                        await save_and_publish(result, db)
                except Exception as exc:
                    logger.error("Error computing signal for %s: %s", symbol, exc)

    try:
        _run(_compute_all())
    except Exception as exc:
        logger.error("refresh_all_signals failed: %s", exc)
        raise self.retry(exc=exc, countdown=10)


@celery_app.task(name="app.tasks.signal_tasks.refresh_signal")
def refresh_signal(symbol: str):
    """Recompute and publish the signal for a single *symbol* (triggered on-demand)."""
    from app.database import task_session
    from app.services.signal_service import save_and_publish
    from app.services.strategy_engine import run_strategy

    async def _compute():
        async with task_session() as db:
            result = await run_strategy(symbol)
            if result:
                await save_and_publish(result, db)

    _run(_compute())


# ─────────────────────────────────────────────────────────────────────────────
# Candle-close listener (runs inside the worker process)
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.on_after_configure.connect
def setup_candle_listener(sender, **kwargs):
    """
    Start an async Redis subscriber inside the Celery worker that fires
    refresh_signal.delay(symbol) every time a candle closes on Binance WS.
    This gives near-real-time signal updates without waiting for the beat tick.
    """
    import threading

    def _listener():
        async def _listen():
            import redis.asyncio as aioredis
            r      = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            pubsub = r.pubsub()
            await pubsub.subscribe("candle_closed")
            logger.info("Candle-close listener started")
            async for msg in pubsub.listen():
                if msg["type"] == "message":
                    try:
                        data   = json.loads(msg["data"])
                        symbol = data.get("symbol")
                        if symbol:
                            refresh_signal.delay(symbol)
                    except Exception as exc:
                        logger.error("Candle listener error: %s", exc)

        loop = asyncio.new_event_loop()
        loop.run_until_complete(_listen())

    t = threading.Thread(target=_listener, daemon=True)
    t.start()
