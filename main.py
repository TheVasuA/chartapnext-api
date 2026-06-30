import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import create_tables
from app.routers import coins, ws, smc, rsi, breakout, signals, scalping, patterns, mtf, swing
from app.services.binance_ws import BinanceWSManager
from app.services.rate_limit import RateLimitMiddleware

# When True, the api process *also* runs the Binance ingestor in its lifespan.
# In production we run a dedicated `ingestor` container instead, so this
# toggle stays False there. Default True for single-container dev setups.
import os
RUN_BINANCE_IN_API = os.getenv("RUN_BINANCE_IN_API", "1") == "1"

binance_manager = BinanceWSManager() if RUN_BINANCE_IN_API else None


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await create_tables()
    if binance_manager is not None:
        asyncio.create_task(binance_manager.start())
    yield
    # Shutdown
    if binance_manager is not None:
        await binance_manager.stop()


app = FastAPI(
    title="ChartAP Signal API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)


# ── Short-TTL cache headers for read-only market-data endpoints ──────────────
# Signals are precomputed by Celery and refreshed every 2–5 min, so a brief
# browser/CDN cache massively cuts origin hits under load without serving stale
# data. `stale-while-revalidate` lets clients show cached data instantly while
# refreshing in the background. We Vary on Authorization so plan-gated responses
# are cached per-token, never leaked across users.
_CACHEABLE_PREFIXES = (
    "/signals", "/smc", "/rsi", "/breakout",
    "/scalping", "/patterns", "/mtf", "/swing",
)


@app.middleware("http")
async def add_cache_headers(request, call_next):
    response = await call_next(request)
    if (
        request.method == "GET"
        and response.status_code == 200
        and "cache-control" not in response.headers
    ):
        path = request.url.path
        if path.startswith("/coins"):
            # Tracked-symbol list barely changes.
            response.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=300"
            response.headers.setdefault("Vary", "Authorization")
        elif any(path.startswith(p) for p in _CACHEABLE_PREFIXES):
            response.headers["Cache-Control"] = "public, max-age=15, stale-while-revalidate=60"
            response.headers.setdefault("Vary", "Authorization")
    return response


app.include_router(coins.router,   prefix="/coins",   tags=["coins"])
app.include_router(smc.router,     prefix="/smc",     tags=["smc"])
app.include_router(rsi.router,     prefix="/rsi",     tags=["rsi"])
app.include_router(signals.router, prefix="/signals", tags=["signals"])
app.include_router(ws.router,                         tags=["websocket"])
app.include_router(breakout.router,  prefix="/breakout",  tags=["breakout"])
app.include_router(scalping.router,  prefix="/scalping",  tags=["scalping"])
app.include_router(patterns.router,  prefix="/patterns",  tags=["patterns"])
app.include_router(mtf.router,       prefix="/mtf",       tags=["mtf"])
app.include_router(swing.router,     prefix="/swing",     tags=["swing"])


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok"}
