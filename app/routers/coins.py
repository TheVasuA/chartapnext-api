from fastapi import APIRouter

from app.utils.symbols import SYMBOLS

router = APIRouter()


@router.get("/")
async def list_coins():
    """Return the list of all tracked symbols."""
    return [{"symbol": s} for s in SYMBOLS]
