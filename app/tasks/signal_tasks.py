"""
Celery app + tasks for SMC signal generation.

Tasks
─────
refresh_all_smc  — recompute SMC analysis for every symbol (beat: every 5 min)
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
        # SMC multi-timeframe signals every 5 minutes (15M timeframe)
        "refresh-all-smc-every-5-minutes": {
            "task":     "app.tasks.signal_tasks.refresh_all_smc",
            "schedule": 300.0,
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
    name="app.tasks.signal_tasks.refresh_all_smc",
    bind=True,
    max_retries=3,
)
def refresh_all_smc(self):
    """Recompute SMC analysis for all symbols and cache results in Redis."""
    from app.redis_client import get_redis
    from app.services.smc_strategy import run_smc_strategy
    from app.utils.symbols import SYMBOLS
    import json

    async def _compute_all():
        redis = await get_redis()
        for symbol in SYMBOLS:
            try:
                result = await run_smc_strategy(symbol)
                if result:
                    await redis.set(
                        f"smc:{symbol}",
                        json.dumps(result),
                        ex=600,   # 10 min TTL
                    )
            except Exception as exc:
                logger.error("Error computing SMC for %s: %s", symbol, exc)

    try:
        _run(_compute_all())
    except Exception as exc:
        logger.error("refresh_all_smc failed: %s", exc)
        raise self.retry(exc=exc, countdown=15)



