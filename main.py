import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import create_tables
from app.routers import coins, ws, smc
from app.services.binance_ws import BinanceWSManager

binance_manager = BinanceWSManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await create_tables()
    asyncio.create_task(binance_manager.start())
    yield
    # Shutdown
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

app.include_router(coins.router,   prefix="/coins",   tags=["coins"])
app.include_router(smc.router,     prefix="/smc",     tags=["smc"])
app.include_router(ws.router,                         tags=["websocket"])


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok"}
