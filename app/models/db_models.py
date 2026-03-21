import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SignalType(str, enum.Enum):
    BUY  = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class Signal(Base):
    __tablename__ = "signals"

    id:          Mapped[int]            = mapped_column(Integer, primary_key=True, index=True)
    symbol:      Mapped[str]            = mapped_column(String(20), index=True, nullable=False)
    signal:      Mapped[SignalType]     = mapped_column(Enum(SignalType), nullable=False)
    price:       Mapped[float]          = mapped_column(Float, nullable=False)
    bb_upper:    Mapped[float | None]   = mapped_column(Float, nullable=True)
    bb_lower:    Mapped[float | None]   = mapped_column(Float, nullable=True)
    interval:    Mapped[str]            = mapped_column(String(5), default="1m")
    created_at:  Mapped[datetime]       = mapped_column(DateTime, default=datetime.utcnow, index=True)
