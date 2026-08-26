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
        # Consolidation breakout scanner every 2 minutes (scalping signals)
        "refresh-scalping-signals-every-2-minutes": {
            "task":     "app.tasks.signal_tasks.refresh_scalping_signals",
            "schedule": 120.0,
        },
        # Consolidation breakout with Telegram alerts every 3 minutes
        "refresh-consolidation-signals-every-3-minutes": {
            "task":     "app.tasks.signal_tasks.refresh_consolidation_signals",
            "schedule": 180.0,
        },
            # Breakout strategy signals every 5 minutes
            "refresh-all-breakout-every-5-minutes": {
                "task":     "app.tasks.signal_tasks.refresh_all_breakout",
                "schedule": 300.0,
            },
        # SMC multi-timeframe signals every 5 minutes (15M timeframe)
        "refresh-all-smc-every-5-minutes": {
            "task":     "app.tasks.signal_tasks.refresh_all_smc",
            "schedule": 300.0,
        },
        # RSI pullback signals every 5 minutes
        "refresh-all-rsi-every-5-minutes": {
            "task":     "app.tasks.signal_tasks.refresh_all_rsi",
            "schedule": 300.0,
        },
        # Generic BUY/SELL/HOLD signals for /signals + WS every 2 minutes
        "refresh-all-signals-every-2-minutes": {
            "task":     "app.tasks.signal_tasks.refresh_all_signals",
            "schedule": 120.0,
        },
        # Classic chart patterns (double top/bottom, H&S, triangles, flags,
        # S/R break+retest) every 5 minutes on the 1h timeframe.
        "refresh-all-patterns-every-5-minutes": {
            "task":     "app.tasks.signal_tasks.refresh_all_patterns",
            "schedule": 300.0,
        },
        # Multi-timeframe RSI+MA+MACD confluence (4h/1h/15m) every 3 minutes.
        "refresh-all-mtf-every-3-minutes": {
            "task":     "app.tasks.signal_tasks.refresh_all_mtf",
            "schedule": 180.0,
        },
        # Swing confluence — Dynamic Trend Matrix on 4h + 15m every 3 minutes.
        "refresh-all-swing-every-3-minutes": {
            "task":     "app.tasks.signal_tasks.refresh_all_swing",
            "schedule": 180.0,
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
    name="app.tasks.signal_tasks.refresh_all_breakout",
    bind=True,
    max_retries=3,
)
def refresh_all_breakout(self):
    """Recompute breakout analysis for all symbols and cache results."""
    from app.redis_client import get_redis
    from app.services.breakout_strategy import run_breakout_strategy
    from app.utils.symbols import SYMBOLS
    import json

    async def _compute_all():
        redis = await get_redis()
        for symbol in SYMBOLS:
            try:
                result = await run_breakout_strategy(symbol)
                if result:
                    await redis.set(
                        f"breakout:{symbol}",
                        json.dumps(result),
                        ex=600,
                    )
            except Exception as exc:
                logger.error("Error computing breakout for %s: %s", symbol, exc)

    try:
        _run(_compute_all())
    except Exception as exc:
        logger.error("refresh_all_breakout failed: %s", exc)
        raise self.retry(exc=exc, countdown=15)


@celery_app.task(
    name="app.tasks.signal_tasks.refresh_all_signals",
    bind=True,
    max_retries=3,
)
def refresh_all_signals(self):
    """Recompute BUY/SELL/HOLD signals for all symbols and publish updates."""
    from app.database import task_session
    from app.services.signal_service import save_and_publish
    from app.services.strategy_engine import run_strategy
    from app.utils.symbols import SYMBOLS

    async def _compute_all():
        async with task_session() as db:
            for symbol in SYMBOLS:
                try:
                    result = await run_strategy(symbol)
                    if result and result.get("price") is not None:
                        await save_and_publish(result, db)
                except Exception as exc:
                    logger.error("Error computing signal for %s: %s", symbol, exc)

    try:
        _run(_compute_all())
    except Exception as exc:
        logger.error("refresh_all_signals failed: %s", exc)
        raise self.retry(exc=exc, countdown=15)


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


@celery_app.task(
    name="app.tasks.signal_tasks.refresh_all_rsi",
    bind=True,
    max_retries=3,
)
def refresh_all_rsi(self):
    """Recompute RSI pullback analysis for all symbols and cache results."""
    from app.redis_client import get_redis
    from app.services.rsi_strategy import run_rsi_strategy
    from app.utils.symbols import SYMBOLS
    import json

    async def _compute_all():
        redis = await get_redis()
        for symbol in SYMBOLS:
            try:
                result = await run_rsi_strategy(symbol)
                if result:
                    await redis.set(
                        f"rsi:{symbol}",
                        json.dumps(result),
                        ex=600,
                    )
            except Exception as exc:
                logger.error("Error computing RSI for %s: %s", symbol, exc)

    try:
        _run(_compute_all())
    except Exception as exc:
        logger.error("refresh_all_rsi failed: %s", exc)
        raise self.retry(exc=exc, countdown=15)


@celery_app.task(
    name="app.tasks.signal_tasks.refresh_scalping_signals",
    bind=True,
    max_retries=3,
)
def refresh_scalping_signals(self):
    """Scan 60 symbols × 4 timeframes for EMA25 consolidation breakouts and
    cache the full signal list in Redis under key 'scalping:signals'."""
    from app.redis_client import get_redis
    from app.services.scalping_strategy import run_scalping_scan
    import json
    import time

    async def _scan():
        signals = await run_scalping_scan()
        redis   = await get_redis()
        payload = json.dumps({
            "signals":      signals,
            "scannedCount": 60,
            "timestamp":    int(time.time() * 1000),
        })
        await redis.set("scalping:signals", payload, ex=300)   # 5 min TTL
        logger.info("refresh_scalping_signals: %d signals cached", len(signals))

    try:
        _run(_scan())
    except Exception as exc:
        logger.error("refresh_scalping_signals failed: %s", exc)
        raise self.retry(exc=exc, countdown=15)


@celery_app.task(
    name="app.tasks.signal_tasks.refresh_all_patterns",
    bind=True,
    max_retries=3,
)
def refresh_all_patterns(self):
    """Detect classic chart patterns for all symbols across 15m/1h/4h and cache."""
    from app.redis_client import get_redis
    from app.services.pattern_detection import run_pattern_scan
    from app.utils.symbols import SYMBOLS
    import json

    intervals = ("15m", "1h", "4h")

    async def _compute_all():
        redis = await get_redis()
        for symbol in SYMBOLS:
            for interval in intervals:
                try:
                    result = await run_pattern_scan(symbol, interval=interval)
                    if result:
                        await redis.set(
                            f"patterns:{symbol}:{interval}",
                            json.dumps(result),
                            ex=900,
                        )
                except Exception as exc:
                    logger.error("Error computing patterns for %s/%s: %s", symbol, interval, exc)

    try:
        _run(_compute_all())
    except Exception as exc:
        logger.error("refresh_all_patterns failed: %s", exc)
        raise self.retry(exc=exc, countdown=15)


@celery_app.task(
    name="app.tasks.signal_tasks.refresh_all_mtf",
    bind=True,
    max_retries=3,
)
def refresh_all_mtf(self):
    """Recompute 4h/1h/15m RSI+MA+MACD confluence for all symbols and cache."""
    from app.redis_client import get_redis
    from app.services.mtf_strategy import run_mtf_strategy
    from app.utils.symbols import SYMBOLS
    import json

    async def _compute_all():
        redis = await get_redis()
        for symbol in SYMBOLS:
            try:
                result = await run_mtf_strategy(symbol)
                if result:
                    await redis.set(f"mtf:{symbol}", json.dumps(result), ex=600)
            except Exception as exc:
                logger.error("Error computing MTF for %s: %s", symbol, exc)

    try:
        _run(_compute_all())
    except Exception as exc:
        logger.error("refresh_all_mtf failed: %s", exc)
        raise self.retry(exc=exc, countdown=15)


@celery_app.task(
    name="app.tasks.signal_tasks.refresh_all_swing",
    bind=True,
    max_retries=3,
)
def refresh_all_swing(self):
    """Recompute 4h+15m Dynamic Trend Matrix confluence for all symbols."""
    from app.redis_client import get_redis
    from app.services.swing_strategy import run_swing_strategy
    from app.utils.symbols import SYMBOLS
    import json

    async def _compute_all():
        redis = await get_redis()
        for symbol in SYMBOLS:
            try:
                result = await run_swing_strategy(symbol)
                if result:
                    await redis.set(f"swing:{symbol}", json.dumps(result), ex=600)
            except Exception as exc:
                logger.error("Error computing swing for %s: %s", symbol, exc)

    try:
        _run(_compute_all())
    except Exception as exc:
        logger.error("refresh_all_swing failed: %s", exc)
        raise self.retry(exc=exc, countdown=15)



@celery_app.task(
    name="app.tasks.signal_tasks.refresh_consolidation_signals",
    bind=True,
    max_retries=3,
)
def refresh_consolidation_signals(self):
    """Scan consolidation breakouts and broadcast new signals via Telegram."""
    from app.redis_client import get_redis
    from app.services.scalping_strategy import run_scalping_scan
    from app.services.telegram_service import broadcast_signals_batch
    import json
    import time

    async def _scan_and_notify():
        signals = await run_scalping_scan()
        redis = await get_redis()

        # Load previous signals to detect NEW ones
        prev_raw = await redis.get("consolidation:signals")
        prev_symbols_tf = set()
        if prev_raw:
            prev_data = json.loads(prev_raw)
            for s in prev_data.get("signals", []):
                prev_symbols_tf.add(f"{s['symbol']}:{s.get('timeframe')}")

        # Identify new signals (not in previous scan)
        new_signals = [
            s for s in signals
            if f"{s['symbol']}:{s.get('timeframe')}" not in prev_symbols_tf
        ]

        # Cache the full result
        payload = json.dumps({
            "signals": signals,
            "scannedCount": 60,
            "timestamp": int(time.time() * 1000),
        })
        await redis.set("consolidation:signals", payload, ex=300)

        # Broadcast NEW signals to Telegram subscribers
        if new_signals:
            subs_raw = await redis.hgetall("consolidation:telegram_subscribers")
            chat_ids = [
                cid if isinstance(cid, str) else cid.decode()
                for cid in subs_raw.keys()
            ]
            if chat_ids:
                sent = await broadcast_signals_batch(new_signals, chat_ids)
                logger.info(
                    "consolidation: %d new signals → Telegram (%d chats, %d sent)",
                    len(new_signals), len(chat_ids), sent,
                )

        logger.info("refresh_consolidation_signals: %d total, %d new", len(signals), len(new_signals))

    try:
        _run(_scan_and_notify())
    except Exception as exc:
        logger.error("refresh_consolidation_signals failed: %s", exc)
        raise self.retry(exc=exc, countdown=15)
