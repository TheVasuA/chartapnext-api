import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import create_tables
from app.routers import coins, ws, smc, rsi, breakout, signals, scalping, patterns
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

app.include_router(coins.router,   prefix="/coins",   tags=["coins"])
app.include_router(smc.router,     prefix="/smc",     tags=["smc"])
app.include_router(rsi.router,     prefix="/rsi",     tags=["rsi"])
app.include_router(signals.router, prefix="/signals", tags=["signals"])
app.include_router(ws.router,                         tags=["websocket"])
app.include_router(breakout.router,  prefix="/breakout",  tags=["breakout"])
app.include_router(scalping.router,  prefix="/scalping",  tags=["scalping"])
app.include_router(patterns.router,  prefix="/patterns",  tags=["patterns"])


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok"}
