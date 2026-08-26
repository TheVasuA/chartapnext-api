"""
Telegram Notification Service
─────────────────────────────
Sends consolidation breakout signals to subscribed Telegram channels/groups.

Environment:
  TELEGRAM_BOT_TOKEN  — BotFather token
  TELEGRAM_CHAT_IDS   — comma-separated default chat IDs to broadcast to
"""

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"
BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")


def _bot_url(method: str) -> str:
    return f"{TELEGRAM_API}/bot{BOT_TOKEN}/{method}"


def _format_signal_message(signal: dict) -> str:
    """Format a consolidation breakout signal into a Telegram-friendly message."""
    is_buy = signal.get("signal") == "BUY"
    emoji = "🟢" if is_buy else "🔴"
    direction = "LONG" if is_buy else "SHORT"

    symbol = signal.get("symbol", "???")
    tf = signal.get("timeframe", "?")
    close = signal.get("close", 0)
    ema25 = signal.get("ema25", 0)
    consol = signal.get("consolCount", 0)
    pct = signal.get("pctFromEma", 0)

    msg = (
        f"{emoji} *Consolidation Breakout — {direction}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 *Symbol:* `{symbol}`\n"
        f"⏱ *Timeframe:* {tf}\n"
        f"💰 *Close:* `{close}`\n"
        f"📊 *EMA25:* `{ema25}`\n"
        f"📐 *Δ EMA:* `{pct:+.3f}%`\n"
        f"🔢 *Consolidation candles:* {consol}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 [TradingView](https://www.tradingview.com/chart/?symbol=BINANCE:{symbol})"
        f" | 🔗 [Binance](https://www.binance.com/en/trade/{symbol.replace('USDT', '')}_USDT)\n"
    )
    return msg


async def send_telegram_message(
    chat_id: str,
    text: str,
    parse_mode: str = "Markdown",
) -> bool:
    """Send a message to a Telegram chat. Returns True on success."""
    if not BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not set — skipping message")
        return False

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                _bot_url("sendMessage"),
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
            if resp.status_code == 200:
                return True
            logger.error("Telegram send failed [%s]: %s", resp.status_code, resp.text)
            return False
    except Exception as exc:
        logger.error("Telegram send exception: %s", exc)
        return False


async def broadcast_signal(signal: dict, chat_ids: Optional[list[str]] = None) -> int:
    """Broadcast a signal to all given chat IDs. Returns count of successful sends."""
    if chat_ids is None:
        raw = os.getenv("TELEGRAM_CHAT_IDS", "")
        chat_ids = [c.strip() for c in raw.split(",") if c.strip()]

    if not chat_ids:
        return 0

    msg = _format_signal_message(signal)
    success = 0
    for cid in chat_ids:
        ok = await send_telegram_message(cid, msg)
        if ok:
            success += 1
    return success


async def broadcast_signals_batch(signals: list[dict], chat_ids: Optional[list[str]] = None) -> int:
    """Broadcast multiple signals. Groups them into a single summary message if > 5."""
    if not signals:
        return 0

    if chat_ids is None:
        raw = os.getenv("TELEGRAM_CHAT_IDS", "")
        chat_ids = [c.strip() for c in raw.split(",") if c.strip()]

    if not chat_ids:
        return 0

    # If few signals, send individual messages
    if len(signals) <= 5:
        total = 0
        for sig in signals:
            total += await broadcast_signal(sig, chat_ids)
        return total

    # Many signals → send a summary
    buy_signals = [s for s in signals if s.get("signal") == "BUY"]
    sell_signals = [s for s in signals if s.get("signal") == "SELL"]

    lines = ["🚨 *Consolidation Breakout Scanner*\n"]
    lines.append(f"Found *{len(signals)}* signals ({len(buy_signals)} BUY, {len(sell_signals)} SELL)\n")

    if buy_signals:
        lines.append("🟢 *LONG Signals:*")
        for s in buy_signals[:10]:
            lines.append(f"  • `{s['symbol']}` ({s.get('timeframe')}) — {s.get('consolCount')} bars")

    if sell_signals:
        lines.append("\n🔴 *SHORT Signals:*")
        for s in sell_signals[:10]:
            lines.append(f"  • `{s['symbol']}` ({s.get('timeframe')}) — {s.get('consolCount')} bars")

    if len(signals) > 10:
        lines.append(f"\n_…and {len(signals) - 10} more_")

    msg = "\n".join(lines)
    success = 0
    for cid in chat_ids:
        ok = await send_telegram_message(cid, msg)
        if ok:
            success += 1
    return success
