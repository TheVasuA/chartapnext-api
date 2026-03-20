"""
Technical indicators computed with pandas_ta.

Indicators computed:
  • RSI  (14)
  • MACD (12, 26, 9)
  • Bollinger Bands (20, 2σ)
  • EMA 20 / EMA 50
"""

from typing import List

import numpy as np
import pandas as pd
import pandas_ta as ta


def build_dataframe(candles: List[dict]) -> pd.DataFrame:
    df = pd.DataFrame(candles)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df.set_index("open_time", inplace=True)
    df.sort_index(inplace=True)
    # Drop duplicate timestamps — keep the last (most recent tick for that candle)
    df = df[~df.index.duplicated(keep="last")]
    return df


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df["rsi"] = ta.rsi(df["close"], length=14)

    macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd is not None:
        df["macd"]       = macd.get("MACD_12_26_9")
        df["macd_signal"] = macd.get("MACDs_12_26_9")
        df["macd_hist"]  = macd.get("MACDh_12_26_9")

    bb = ta.bbands(df["close"], length=20, std=2)
    if bb is not None:
        df["bb_upper"] = bb.get("BBU_20_2.0")
        df["bb_mid"]   = bb.get("BBM_20_2.0")
        df["bb_lower"] = bb.get("BBL_20_2.0")

    df["ema_20"] = ta.ema(df["close"], length=20)
    df["ema_50"] = ta.ema(df["close"], length=50)

    return df


def safe_float(val) -> float | None:
    """Convert a pandas/numpy scalar to Python float, returning None for NaN."""
    if val is None:
        return None
    try:
        return None if np.isnan(float(val)) else float(val)
    except (TypeError, ValueError):
        return None
