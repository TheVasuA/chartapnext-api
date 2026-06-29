"""
Classic chart-pattern detection.
─────────────────────────────────────────────────────────────────────────────
Swing/pivot detection via scipy.signal.find_peaks, then geometric rule
matching for the highest signal-to-noise reversal / continuation patterns:

  • Double Top / Double Bottom        (reversal)
  • Head & Shoulders / Inverse H&S    (reversal)
  • Triangles & Wedges                (continuation / reversal)
  • Bull / Bear Flag                  (continuation)
  • Support / Resistance break + retest

Each detector returns a dict describing the pattern *and* the geometry the
frontend can draw directly over the candle window:
    markers : pivots to dot      [{i, price, kind, label}]
    lines   : neckline/trendlines[{from:[i,price], to:[i,price], kind, label}]
    levels  : horizontal levels  [{price, kind, label}]   (entry/target/stop)

Tolerances follow widely-cited references — e.g. Bulkowski's ~6% maximum
separation between the two tops of a double-top, and the neckline
measured-move target for head & shoulders (pattern height projected from
the breakout). All indices in `markers`/`lines` are positions within the
returned `window.candles` array.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import List, Optional

import numpy as np
from scipy.signal import find_peaks

from app.services.indicators import build_dataframe
from app.services.market_data import get_ohlcv_multi

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Tuning
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PatternConfig:
    lookback: int = 90              # candles in the analysis / snapshot window
    swing_distance: int = 3         # min bars between swings (find_peaks distance)
    swing_prominence_pct: float = 0.012   # prominence as fraction of price range
    peak_tol: float = 0.05          # max relative diff between the two tops/bottoms
    shoulder_tol: float = 0.06      # max relative diff between H&S shoulders
    min_depth: float = 0.025        # min valley/peak depth vs pattern height
    max_age: int = 8                # last pivot must be within N bars of window end
    sr_tol: float = 0.012           # cluster tolerance for S/R levels
    sr_min_touches: int = 3
    flat_slope: float = 0.0006      # |slope/price| per bar considered "flat"

    # ── Quality filters (added for higher-probability, higher-profit signals) ──
    atr_period: int = 14            # ATR window for volatility-aware stops
    atr_stop_mult: float = 1.2      # min stop distance = atr_stop_mult × ATR
    vol_lookback: int = 20          # bars to average volume over
    vol_confirm_ratio: float = 1.5  # breakout-bar volume ≥ this × avg ⇒ confirmed
    min_risk_reward: float = 1.5    # drop setups whose R:R is below this
    min_confidence: float = 0.45    # drop low-quality detections
    # Confidence blend weights (geometry / volume / trend / momentum)
    w_geometry: float = 0.45
    w_volume: float = 0.25
    w_trend: float = 0.18
    w_momentum: float = 0.12


CFG = PatternConfig()


# ─────────────────────────────────────────────────────────────────────────────
# Swing detection + helpers
# ─────────────────────────────────────────────────────────────────────────────

def find_swings(highs: np.ndarray, lows: np.ndarray, cfg: PatternConfig):
    """Return (peak_indices, trough_indices) using scipy.signal.find_peaks.

    Prominence is scaled to the price range so it adapts across symbols and
    price magnitudes. `distance` enforces a minimum bar separation so we pick
    structural swings, not micro-noise.
    """
    rng = float(np.nanmax(highs) - np.nanmin(lows))
    prom = max(rng * cfg.swing_prominence_pct, 1e-12)
    peaks, _ = find_peaks(highs, distance=cfg.swing_distance, prominence=prom)
    troughs, _ = find_peaks(-lows, distance=cfg.swing_distance, prominence=prom)
    return peaks.tolist(), troughs.tolist()


def _df_to_window(df) -> List[dict]:
    candles = []
    for ts, row in df.iterrows():
        candles.append({
            "t": int(ts.value // 1_000_000),
            "o": round(float(row["open"]), 8),
            "h": round(float(row["high"]), 8),
            "l": round(float(row["low"]), 8),
            "c": round(float(row["close"]), 8),
        })
    return candles


def _result(pattern, label, direction, status, confidence, price,
            entry, target, stop, markers, lines, levels) -> dict:
    # Coerce numpy scalars → plain Python floats so the dict is JSON-serializable.
    def f(v):
        return None if v is None else float(v)

    confidence = f(confidence)
    price = f(price)
    entry = f(entry)
    target = f(target)
    stop = f(stop)

    rr = None
    if entry and stop and target and abs(entry - stop) > 1e-12:
        rr = round(abs(target - entry) / abs(entry - stop), 2)
    return {
        "pattern": pattern,
        "label": label,
        "direction": direction,        # bullish | bearish | neutral
        "status": status,              # forming | confirmed
        "confidence": confidence,      # 0..1
        "price": price,
        "entry": entry,
        "target": target,
        "stop": stop,
        "risk_reward": rr,
        "markers": markers,
        "lines": lines,
        "levels": levels,
    }


def _fit_line(xs, ys):
    if len(xs) < 2:
        return None
    slope, intercept = np.polyfit(np.asarray(xs, float), np.asarray(ys, float), 1)
    return float(slope), float(intercept)


# ─────────────────────────────────────────────────────────────────────────────
# Reversal patterns
# ─────────────────────────────────────────────────────────────────────────────

def detect_double_top(highs, lows, closes, peaks, troughs, n, cfg) -> Optional[dict]:
    if len(peaks) < 2:
        return None
    p1, p2 = peaks[-2], peaks[-1]
    if (n - 1) - p2 > cfg.max_age:
        return None
    h1, h2 = highs[p1], highs[p2]
    top = max(h1, h2)
    if abs(h1 - h2) / top > cfg.peak_tol:
        return None
    mids = [t for t in troughs if p1 < t < p2]
    if not mids:
        return None
    valley = min(mids, key=lambda t: lows[t])
    vlow = lows[valley]
    avg_top = (h1 + h2) / 2
    depth = (avg_top - vlow) / avg_top
    if depth < cfg.min_depth:
        return None

    neckline = float(vlow)
    last_close = float(closes[-1])
    confirmed = last_close < neckline
    height = avg_top - neckline
    target = neckline - height
    stop = float(top * 1.005)

    sim = 1 - (abs(h1 - h2) / top) / cfg.peak_tol
    rec = 1 - ((n - 1) - p2) / cfg.max_age
    conf = max(0.0, min(1.0, 0.4 * sim + 0.3 * min(depth / 0.08, 1) + 0.3 * rec))
    if confirmed:
        conf = min(1.0, conf + 0.15)

    markers = [
        {"i": int(p1), "price": float(h1), "kind": "peak", "label": "Top 1"},
        {"i": int(p2), "price": float(h2), "kind": "peak", "label": "Top 2"},
        {"i": int(valley), "price": float(vlow), "kind": "trough", "label": ""},
    ]
    lines = [{"from": [int(p1), neckline], "to": [n - 1, neckline], "kind": "neckline", "label": "Neckline"}]
    levels = [
        {"price": neckline, "kind": "entry", "label": "Entry (break)"},
        {"price": float(target), "kind": "target", "label": "Target"},
        {"price": stop, "kind": "stop", "label": "Stop"},
    ]
    return _result("double_top", "Double Top", "bearish",
                   "confirmed" if confirmed else "forming", round(conf, 2),
                   last_close, neckline, float(target), stop, markers, lines, levels)


def detect_double_bottom(highs, lows, closes, peaks, troughs, n, cfg) -> Optional[dict]:
    if len(troughs) < 2:
        return None
    b1, b2 = troughs[-2], troughs[-1]
    if (n - 1) - b2 > cfg.max_age:
        return None
    l1, l2 = lows[b1], lows[b2]
    if abs(l1 - l2) / max(l1, l2) > cfg.peak_tol:
        return None
    mids = [p for p in peaks if b1 < p < b2]
    if not mids:
        return None
    peak = max(mids, key=lambda p: highs[p])
    phigh = highs[peak]
    avg_bot = (l1 + l2) / 2
    height = phigh - avg_bot
    depth = height / phigh
    if depth < cfg.min_depth:
        return None

    neckline = float(phigh)
    last_close = float(closes[-1])
    confirmed = last_close > neckline
    target = neckline + height
    stop = float(min(l1, l2) * 0.995)

    sim = 1 - (abs(l1 - l2) / max(l1, l2)) / cfg.peak_tol
    rec = 1 - ((n - 1) - b2) / cfg.max_age
    conf = max(0.0, min(1.0, 0.4 * sim + 0.3 * min(depth / 0.08, 1) + 0.3 * rec))
    if confirmed:
        conf = min(1.0, conf + 0.15)

    markers = [
        {"i": int(b1), "price": float(l1), "kind": "trough", "label": "Bottom 1"},
        {"i": int(b2), "price": float(l2), "kind": "trough", "label": "Bottom 2"},
        {"i": int(peak), "price": float(phigh), "kind": "peak", "label": ""},
    ]
    lines = [{"from": [int(b1), neckline], "to": [n - 1, neckline], "kind": "neckline", "label": "Neckline"}]
    levels = [
        {"price": neckline, "kind": "entry", "label": "Entry (break)"},
        {"price": float(target), "kind": "target", "label": "Target"},
        {"price": stop, "kind": "stop", "label": "Stop"},
    ]
    return _result("double_bottom", "Double Bottom", "bullish",
                   "confirmed" if confirmed else "forming", round(conf, 2),
                   last_close, neckline, float(target), stop, markers, lines, levels)


def detect_head_shoulders(highs, lows, closes, peaks, troughs, n, cfg) -> Optional[dict]:
    if len(peaks) < 3:
        return None
    ls, hd, rs = peaks[-3], peaks[-2], peaks[-1]
    if (n - 1) - rs > cfg.max_age + 4:
        return None
    hls, hhd, hrs = highs[ls], highs[hd], highs[rs]
    if not (hhd > hls and hhd > hrs):
        return None
    if (hhd - max(hls, hrs)) / hhd < 0.01:        # head must clear shoulders
        return None
    if abs(hls - hrs) / hhd > cfg.shoulder_tol:    # shoulders roughly level
        return None

    a1c = [t for t in troughs if ls < t < hd]
    a2c = [t for t in troughs if hd < t < rs]
    if not a1c or not a2c:
        return None
    a1 = min(a1c, key=lambda t: lows[t])
    a2 = min(a2c, key=lambda t: lows[t])
    l1, l2 = lows[a1], lows[a2]
    slope = (l2 - l1) / (a2 - a1) if a2 != a1 else 0.0

    def neck(x):
        return l1 + slope * (x - a1)

    neck_now = float(neck(n - 1))
    last_close = float(closes[-1])
    confirmed = last_close < neck_now
    height = hhd - neck(hd)
    target = neck_now - height
    stop = float(hhd * 1.005)

    sym = 1 - (abs(hls - hrs) / hhd) / cfg.shoulder_tol
    rec = 1 - min(((n - 1) - rs) / (cfg.max_age + 4), 1)
    conf = max(0.0, min(1.0, 0.4 * sym + 0.3 * min((height / hhd) / 0.1, 1) + 0.3 * rec))
    if confirmed:
        conf = min(1.0, conf + 0.15)

    markers = [
        {"i": int(ls), "price": float(hls), "kind": "peak", "label": "L Shoulder"},
        {"i": int(hd), "price": float(hhd), "kind": "peak", "label": "Head"},
        {"i": int(rs), "price": float(hrs), "kind": "peak", "label": "R Shoulder"},
        {"i": int(a1), "price": float(l1), "kind": "trough", "label": ""},
        {"i": int(a2), "price": float(l2), "kind": "trough", "label": ""},
    ]
    lines = [{"from": [int(a1), float(l1)], "to": [n - 1, neck_now], "kind": "neckline", "label": "Neckline"}]
    levels = [
        {"price": neck_now, "kind": "entry", "label": "Entry"},
        {"price": float(target), "kind": "target", "label": "Target"},
        {"price": stop, "kind": "stop", "label": "Stop"},
    ]
    return _result("head_shoulders", "Head & Shoulders", "bearish",
                   "confirmed" if confirmed else "forming", round(conf, 2),
                   last_close, neck_now, float(target), stop, markers, lines, levels)


def detect_inverse_hs(highs, lows, closes, peaks, troughs, n, cfg) -> Optional[dict]:
    if len(troughs) < 3:
        return None
    ls, hd, rs = troughs[-3], troughs[-2], troughs[-1]
    if (n - 1) - rs > cfg.max_age + 4:
        return None
    lls, lhd, lrs = lows[ls], lows[hd], lows[rs]
    if not (lhd < lls and lhd < lrs):
        return None
    if (min(lls, lrs) - lhd) / min(lls, lrs) < 0.01:
        return None
    if abs(lls - lrs) / lhd > cfg.shoulder_tol:
        return None

    a1c = [p for p in peaks if ls < p < hd]
    a2c = [p for p in peaks if hd < p < rs]
    if not a1c or not a2c:
        return None
    a1 = max(a1c, key=lambda p: highs[p])
    a2 = max(a2c, key=lambda p: highs[p])
    h1, h2 = highs[a1], highs[a2]
    slope = (h2 - h1) / (a2 - a1) if a2 != a1 else 0.0

    def neck(x):
        return h1 + slope * (x - a1)

    neck_now = float(neck(n - 1))
    last_close = float(closes[-1])
    confirmed = last_close > neck_now
    height = neck(hd) - lhd
    target = neck_now + height
    stop = float(lhd * 0.995)

    sym = 1 - (abs(lls - lrs) / lhd) / cfg.shoulder_tol
    rec = 1 - min(((n - 1) - rs) / (cfg.max_age + 4), 1)
    conf = max(0.0, min(1.0, 0.4 * sym + 0.3 * min((height / lhd) / 0.1, 1) + 0.3 * rec))
    if confirmed:
        conf = min(1.0, conf + 0.15)

    markers = [
        {"i": int(ls), "price": float(lls), "kind": "trough", "label": "L Shoulder"},
        {"i": int(hd), "price": float(lhd), "kind": "trough", "label": "Head"},
        {"i": int(rs), "price": float(lrs), "kind": "trough", "label": "R Shoulder"},
        {"i": int(a1), "price": float(h1), "kind": "peak", "label": ""},
        {"i": int(a2), "price": float(h2), "kind": "peak", "label": ""},
    ]
    lines = [{"from": [int(a1), float(h1)], "to": [n - 1, neck_now], "kind": "neckline", "label": "Neckline"}]
    levels = [
        {"price": neck_now, "kind": "entry", "label": "Entry"},
        {"price": float(target), "kind": "target", "label": "Target"},
        {"price": stop, "kind": "stop", "label": "Stop"},
    ]
    return _result("inverse_head_shoulders", "Inverse Head & Shoulders", "bullish",
                   "confirmed" if confirmed else "forming", round(conf, 2),
                   last_close, neck_now, float(target), stop, markers, lines, levels)


# ─────────────────────────────────────────────────────────────────────────────
# Triangles & Wedges
# ─────────────────────────────────────────────────────────────────────────────

def detect_triangle(highs, lows, closes, peaks, troughs, n, cfg) -> Optional[dict]:
    ph = peaks[-3:] if len(peaks) >= 3 else peaks
    pl = troughs[-3:] if len(troughs) >= 3 else troughs
    if len(ph) < 2 or len(pl) < 2:
        return None

    fh = _fit_line(ph, [highs[i] for i in ph])
    fl = _fit_line(pl, [lows[i] for i in pl])
    if not fh or not fl:
        return None
    sh, ih = fh
    sl, il = fl

    avg_price = float(np.mean(closes))
    sh_n, sl_n = sh / avg_price, sl / avg_price

    x0 = min(ph[0], pl[0])
    x1 = n - 1
    upper0, lower0 = sh * x0 + ih, sl * x0 + il
    upper1, lower1 = sh * x1 + ih, sl * x1 + il
    gap0, gap1 = upper0 - lower0, upper1 - lower1
    converging = gap1 < gap0 * 0.85 and gap1 > 0
    flat = cfg.flat_slope

    pattern = direction = label = None
    if abs(sh_n) < flat and sl_n > flat:
        pattern, direction, label = "ascending_triangle", "bullish", "Ascending Triangle"
    elif abs(sl_n) < flat and sh_n < -flat:
        pattern, direction, label = "descending_triangle", "bearish", "Descending Triangle"
    elif sh_n < -flat and sl_n > flat and converging:
        pattern, direction, label = "symmetrical_triangle", "neutral", "Symmetrical Triangle"
    elif sh_n > flat and sl_n > flat and converging and sl_n > sh_n:
        pattern, direction, label = "rising_wedge", "bearish", "Rising Wedge"
    elif sh_n < -flat and sl_n < -flat and converging and sh_n > sl_n:
        pattern, direction, label = "falling_wedge", "bullish", "Falling Wedge"
    else:
        return None

    last_close = float(closes[-1])
    status = "forming"
    if last_close > upper1 and pattern in ("ascending_triangle", "symmetrical_triangle", "falling_wedge"):
        status = "confirmed"
        if direction == "neutral":
            direction = "bullish"
    elif last_close < lower1 and pattern in ("descending_triangle", "symmetrical_triangle", "rising_wedge"):
        status = "confirmed"
        if direction == "neutral":
            direction = "bearish"

    height = gap0
    entry = target = stop = None
    if direction == "bullish":
        entry, target, stop = float(upper1), float(upper1 + height), float(lower1)
    elif direction == "bearish":
        entry, target, stop = float(lower1), float(lower1 - height), float(upper1)

    conf = 0.4 + (0.2 if converging else 0.0) + (0.2 if status == "confirmed" else 0.0)
    conf = min(conf, 0.9)

    markers = [{"i": int(i), "price": float(highs[i]), "kind": "peak", "label": ""} for i in ph] + \
              [{"i": int(i), "price": float(lows[i]), "kind": "trough", "label": ""} for i in pl]
    lines = [
        {"from": [int(x0), float(upper0)], "to": [int(x1), float(upper1)], "kind": "trend", "label": "Resistance"},
        {"from": [int(x0), float(lower0)], "to": [int(x1), float(lower1)], "kind": "trend", "label": "Support"},
    ]
    levels = []
    if target is not None:
        levels = [
            {"price": entry, "kind": "entry", "label": "Entry"},
            {"price": target, "kind": "target", "label": "Target"},
            {"price": stop, "kind": "stop", "label": "Stop"},
        ]
    return _result(pattern, label, direction, status, round(conf, 2),
                   last_close, entry, target, stop, markers, lines, levels)


# ─────────────────────────────────────────────────────────────────────────────
# Flags
# ─────────────────────────────────────────────────────────────────────────────

def detect_flag(highs, lows, closes, peaks, troughs, n, cfg) -> Optional[dict]:
    pole_len, flag_len = 6, 6
    cons_start = n - flag_len
    pole_start = cons_start - pole_len
    if pole_start < 0:
        return None

    pole_ret = (closes[cons_start - 1] - closes[pole_start]) / closes[pole_start]
    cons_high = float(np.max(highs[cons_start:]))
    cons_low = float(np.min(lows[cons_start:]))
    cons_range = (cons_high - cons_low) / closes[cons_start - 1]

    if pole_ret > 0.06 and cons_range < abs(pole_ret) * 0.6:
        pattern, direction, label = "bull_flag", "bullish", "Bull Flag"
        entry = cons_high
        height = closes[cons_start - 1] - closes[pole_start]
        target, stop = entry + height, cons_low
    elif pole_ret < -0.06 and cons_range < abs(pole_ret) * 0.6:
        pattern, direction, label = "bear_flag", "bearish", "Bear Flag"
        entry = cons_low
        height = closes[pole_start] - closes[cons_start - 1]
        target, stop = entry - height, cons_high
    else:
        return None

    last_close = float(closes[-1])
    confirmed = (direction == "bullish" and last_close > entry) or \
                (direction == "bearish" and last_close < entry)
    conf = min(0.5 + (0.2 if confirmed else 0.0) + min(abs(pole_ret), 0.2), 0.9)

    markers = [
        {"i": int(pole_start), "price": float(closes[pole_start]),
         "kind": "trough" if direction == "bullish" else "peak", "label": "Pole"},
        {"i": int(cons_start - 1), "price": float(closes[cons_start - 1]),
         "kind": "peak" if direction == "bullish" else "trough", "label": ""},
    ]
    lines = [{"from": [int(pole_start), float(closes[pole_start])],
              "to": [int(cons_start - 1), float(closes[cons_start - 1])],
              "kind": "trend", "label": "Pole"}]
    levels = [
        {"price": float(entry), "kind": "entry", "label": "Entry"},
        {"price": float(target), "kind": "target", "label": "Target"},
        {"price": float(stop), "kind": "stop", "label": "Stop"},
    ]
    return _result(pattern, label, direction, "confirmed" if confirmed else "forming",
                   round(conf, 2), last_close, float(entry), float(target), float(stop),
                   markers, lines, levels)


# ─────────────────────────────────────────────────────────────────────────────
# Support / Resistance break + retest
# ─────────────────────────────────────────────────────────────────────────────

def detect_sr_retest(highs, lows, closes, peaks, troughs, n, cfg) -> Optional[dict]:
    pivots = [(p, float(highs[p])) for p in peaks] + [(t, float(lows[t])) for t in troughs]
    if len(pivots) < cfg.sr_min_touches:
        return None

    ordered = sorted(pivots, key=lambda x: x[1])
    clusters, cur = [], [ordered[0]]
    for piv in ordered[1:]:
        if abs(piv[1] - cur[-1][1]) / cur[-1][1] <= cfg.sr_tol:
            cur.append(piv)
        else:
            clusters.append(cur)
            cur = [piv]
    clusters.append(cur)

    clusters = [c for c in clusters if len(c) >= cfg.sr_min_touches]
    if not clusters:
        return None
    best = max(clusters, key=len)
    level = float(np.mean([p[1] for p in best]))

    buf = level * cfg.sr_tol
    recent = closes[-10:]
    broke_up = closes[-1] > level + buf and float(np.min(recent)) < level
    broke_down = closes[-1] < level - buf and float(np.max(recent)) > level
    near_level = any(abs(c - level) / level <= cfg.sr_tol for c in closes[-5:])

    # Measured move = height of the recent trading range projected from the
    # broken level. Use the last ~30 bars for the opposite boundary so the
    # target reflects the consolidation, not an old trend leg.
    seg0 = max(0, n - 30)
    range_high = float(np.max(highs[seg0:]))
    range_low = float(np.min(lows[seg0:]))

    if broke_up:
        direction, pattern = "bullish", "resistance_break"
        label = "Resistance Break + Retest" if near_level else "Resistance Breakout"
        entry = level
        height = max(level - range_low, level * 0.02)   # floor at 2%
        target = level + height
        stop = level - buf * 2
    elif broke_down:
        direction, pattern = "bearish", "support_break"
        label = "Support Break + Retest" if near_level else "Support Breakdown"
        entry = level
        height = max(range_high - level, level * 0.02)
        target = level - height
        stop = level + buf * 2
    else:
        return None

    last_close = float(closes[-1])
    status = "confirmed" if near_level else "forming"
    conf = min(0.4 + 0.1 * len(best) + (0.15 if near_level else 0.0), 0.9)

    markers = [{"i": int(p[0]), "price": float(p[1]), "kind": "level", "label": ""} for p in best]
    lines = [{"from": [int(best[0][0]), level], "to": [n - 1, level], "kind": "sr", "label": "S/R Level"}]
    levels = [
        {"price": float(entry), "kind": "entry", "label": "Level"},
        {"price": float(target), "kind": "target", "label": "Target"},
        {"price": float(stop), "kind": "stop", "label": "Stop"},
    ]
    return _result(pattern, label, direction, status, round(conf, 2),
                   last_close, float(entry), float(target), float(stop), markers, lines, levels)


# ─────────────────────────────────────────────────────────────────────────────
# Quality scoring — volume, trend, momentum, ATR stops, R:R filter
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MarketContext:
    atr: float            # latest ATR (price units)
    vol_ratio: float      # last-bar volume / average volume
    vol_rising: bool      # volume trending up into the breakout
    trend: str            # 'up' | 'down' | 'side'  (EMA20 vs EMA50)
    rsi: float            # latest RSI(14)


def _ema(arr: np.ndarray, length: int) -> float:
    if len(arr) < length:
        return float(arr[-1]) if len(arr) else 0.0
    k = 2 / (length + 1)
    e = float(arr[0])
    for v in arr[1:]:
        e = float(v) * k + e * (1 - k)
    return e


def _atr(highs, lows, closes, period: int) -> float:
    n = len(closes)
    if n < 2:
        return 0.0
    trs = []
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    period = min(period, len(trs))
    return float(np.mean(trs[-period:])) if trs else 0.0


def _rsi(closes: np.ndarray, period: int = 14) -> float:
    if len(closes) <= period:
        return 50.0
    diff = np.diff(closes)
    gains = np.where(diff > 0, diff, 0.0)
    losses = np.where(diff < 0, -diff, 0.0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return float(100 - 100 / (1 + rs))


def build_context(highs, lows, closes, volumes, cfg: PatternConfig) -> MarketContext:
    atr = _atr(highs, lows, closes, cfg.atr_period)
    if volumes is not None and len(volumes) >= cfg.vol_lookback + 1:
        avg_vol = float(np.mean(volumes[-cfg.vol_lookback - 1:-1])) or 1e-12
        vol_ratio = float(volumes[-1]) / avg_vol
        first_half = float(np.mean(volumes[-cfg.vol_lookback:-cfg.vol_lookback // 2]))
        second_half = float(np.mean(volumes[-cfg.vol_lookback // 2:]))
        vol_rising = second_half >= first_half
    else:
        vol_ratio, vol_rising = 1.0, False

    ema_fast = _ema(closes[-50:], 20)
    ema_slow = _ema(closes[-50:], 50)
    last = float(closes[-1])
    if ema_fast > ema_slow and last >= ema_slow:
        trend = "up"
    elif ema_fast < ema_slow and last <= ema_slow:
        trend = "down"
    else:
        trend = "side"

    return MarketContext(atr=atr, vol_ratio=vol_ratio, vol_rising=vol_rising,
                         trend=trend, rsi=_rsi(closes, 14))


# Patterns that act as trend *continuation* (should align with prevailing trend)
_CONTINUATION = {
    "ascending_triangle", "descending_triangle", "symmetrical_triangle",
    "bull_flag", "bear_flag", "resistance_break", "support_break",
}


def _quality_grade(conf: float) -> str:
    if conf >= 0.75:
        return "A"
    if conf >= 0.6:
        return "B"
    return "C"


def enrich_and_filter(patterns: List[dict], ctx: MarketContext, cfg: PatternConfig) -> List[dict]:
    """Re-score each detected pattern with volume / trend / momentum / ATR,
    widen too-tight stops to an ATR floor, recompute R:R, and drop setups that
    don't clear the minimum R:R and confidence bars. This is what turns raw
    geometric hits into higher-probability, higher-profit signals."""
    out: List[dict] = []
    for p in patterns:
        direction = p.get("direction")
        entry = p.get("entry")
        target = p.get("target")
        stop = p.get("stop")
        reasons: List[str] = []

        # 1) Volatility-aware stop: never tighter than atr_stop_mult × ATR.
        if entry and stop and ctx.atr > 0 and direction in ("bullish", "bearish"):
            min_dist = cfg.atr_stop_mult * ctx.atr
            if abs(entry - stop) < min_dist:
                stop = entry - min_dist if direction == "bullish" else entry + min_dist
                p["stop"] = round(float(stop), 8)
                reasons.append("Stop widened to ATR floor")

        # Recompute R:R after stop adjustment.
        rr = None
        if entry and stop and target and abs(entry - stop) > 1e-12:
            rr = round(abs(target - entry) / abs(entry - stop), 2)
            p["risk_reward"] = rr

        # 2) Volume confirmation.
        vol_score = max(0.0, min(ctx.vol_ratio / cfg.vol_confirm_ratio, 1.0))
        if ctx.vol_ratio >= cfg.vol_confirm_ratio:
            reasons.append(f"Volume {ctx.vol_ratio:.1f}× avg")
        elif ctx.vol_ratio < 0.8:
            reasons.append("Low volume — weak confirmation")
        if ctx.vol_rising:
            vol_score = min(1.0, vol_score + 0.1)

        # 3) Trend alignment.
        is_cont = p.get("pattern") in _CONTINUATION
        trend_score = 0.5
        if direction == "bullish":
            if ctx.trend == "up":
                trend_score = 1.0
                reasons.append("Aligned with uptrend")
            elif ctx.trend == "down" and is_cont:
                trend_score = 0.15
                reasons.append("Against trend")
        elif direction == "bearish":
            if ctx.trend == "down":
                trend_score = 1.0
                reasons.append("Aligned with downtrend")
            elif ctx.trend == "up" and is_cont:
                trend_score = 0.15
                reasons.append("Against trend")

        # 4) Momentum (RSI): reward room-to-run, penalise exhaustion.
        mom_score = 0.5
        if direction == "bullish":
            if 45 <= ctx.rsi <= 68:
                mom_score = 1.0
            elif ctx.rsi > 78:
                mom_score = 0.2
                reasons.append("RSI overbought")
        elif direction == "bearish":
            if 32 <= ctx.rsi <= 55:
                mom_score = 1.0
            elif ctx.rsi < 22:
                mom_score = 0.2
                reasons.append("RSI oversold")

        base = float(p.get("confidence", 0.5))
        conf = (
            cfg.w_geometry * base
            + cfg.w_volume * vol_score
            + cfg.w_trend * trend_score
            + cfg.w_momentum * mom_score
        )
        if p.get("status") == "confirmed":
            conf = min(1.0, conf + 0.08)
        conf = max(0.0, min(1.0, conf))

        # Annotate.
        p["confidence"] = round(conf, 2)
        p["quality"] = _quality_grade(conf)
        p["volume_ratio"] = round(ctx.vol_ratio, 2)
        p["volume_confirmed"] = ctx.vol_ratio >= cfg.vol_confirm_ratio
        p["trend"] = ctx.trend
        p["rsi"] = round(ctx.rsi, 1)
        p["atr"] = round(ctx.atr, 8)
        p["reasons"] = reasons

        # 5) Hard filters — only high-probability, high-profit setups survive.
        if rr is not None and rr < cfg.min_risk_reward:
            continue
        if conf < cfg.min_confidence:
            continue
        out.append(p)

    out.sort(key=lambda x: x["confidence"], reverse=True)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────────────

_DETECTORS = (
    detect_double_top,
    detect_double_bottom,
    detect_head_shoulders,
    detect_inverse_hs,
    detect_triangle,
    detect_flag,
    detect_sr_retest,
)


async def run_pattern_scan(symbol: str, interval: str = "1h", limit: int = 200) -> Optional[dict]:
    """Detect classic chart patterns for *symbol* on *interval*.

    Returns a dict with the candle `window` (for drawing) and a `patterns`
    list sorted by confidence (best first). None when there's not enough data.
    """
    (candles_list,) = await get_ohlcv_multi(symbol, intervals=[interval], limits=[limit])
    if len(candles_list) < 40:
        return None

    df = build_dataframe(candles_list)
    df = df.iloc[-CFG.lookback:]
    if len(df) < 30:
        return None

    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    volumes = df["volume"].to_numpy(dtype=float) if "volume" in df else None
    n = len(closes)

    peaks, troughs = find_swings(highs, lows, CFG)
    window = _df_to_window(df)

    found: List[dict] = []
    for detector in _DETECTORS:
        try:
            res = detector(highs, lows, closes, peaks, troughs, n, CFG)
            if res:
                found.append(res)
        except Exception as exc:  # never let one detector kill the scan
            logger.debug("%s failed for %s/%s: %s", detector.__name__, symbol, interval, exc)

    # Re-score with volume / trend / momentum / ATR and keep only the
    # high-probability, high-R:R setups.
    ctx = build_context(highs, lows, closes, volumes, CFG)
    found = enrich_and_filter(found, ctx, CFG)

    return {
        "symbol": symbol,
        "interval": interval,
        "timestamp": int(time.time() * 1000),
        "patterns": found,
        "window": {"interval": interval, "candles": window},
    }
