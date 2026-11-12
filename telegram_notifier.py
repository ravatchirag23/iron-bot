"""
Telegram Notification Engine for Iron Bot
Sends instant alerts to Chirag's phone when trades trigger, trail, or close.
"""

import urllib.request
import urllib.parse
import json
import logging
import ssl
from typing import Optional
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger("IronBot.Telegram")

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE

def send_telegram_alert(message: str) -> bool:
    """Send formatted markdown alert to Telegram. Falls back gracefully if no token configured."""
    print(f"\n📢 [IRON BOT ALERT]\n{message}\n" + "-"*45, flush=True)
    
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[TELEGRAM ERROR] Missing credentials. Token: {'SET' if TELEGRAM_BOT_TOKEN else 'MISSING'}, ChatID: {'SET' if TELEGRAM_CHAT_ID else 'MISSING'}", flush=True)
        return False
        
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        
        with urllib.request.urlopen(req, context=_ssl_ctx, timeout=10) as response:
            result = response.read().decode("utf-8")
            print(f"[TELEGRAM SUCCESS] Status: {response.status} | Result: {result}", flush=True)
            return response.status == 200
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        print(f"[TELEGRAM HTTP ERROR] Code: {e.code} | Reason: {err_body}", flush=True)
        logger.error(f"Telegram HTTP Error {e.code}: {err_body}")
        return False
    except Exception as e:
        print(f"[TELEGRAM GENERAL ERROR] {e}", flush=True)
        logger.error(f"Failed to send Telegram message: {e}")
        return False

def notify_entry(symbol: str, signal_type: str, entry_price: float, sl_price: float, target_price: float, quantity: int, oi_advisory: str = "", is_runner: bool = False):
    risk_pts = round(entry_price - sl_price if "BUY" in signal_type or "CE" in signal_type else sl_price - entry_price, 1)
    risk_rupees = round(risk_pts * quantity, 2)
    
    safe_advisory = oi_advisory.replace("_", " ")
    advisory_line = f"🧭 *Market Structure:* {safe_advisory}\n" if oi_advisory else ""
    target_line = f"🚀 *Target:* 🌊 *UNLIMITED TREND RUNNER* (Ratchet Trailing)\n" if is_runner else f"🚀 *Target:* ₹{target_price:.2f} (+{target_price - entry_price:.1f} pts)\n"

    try:
        import database
        import config
        stats = database.get_today_stats()
        trades_done = stats.get("trade_count", 1)
        max_trades = getattr(config, "MAX_DAILY_TRADES", 4)
        status_line = f"🔒 *Status:* Trade {trades_done}/{max_trades} Active. Discipline locks ON!"
    except Exception:
        status_line = "🔒 *Status:* Trade Active. Discipline locks ON!"

    msg = (
        f"🤖 *IRON BOT: NEW TRADE TRIGGERED*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 *Signal:* `{signal_type}`\n"
        f"🎯 *Strike:* `{symbol}` (1 Lot: {quantity} Qty)\n"
        f"💵 *Entry Price:* ₹{entry_price:.2f}\n"
        f"🛑 *Micro-SL:* ₹{sl_price:.2f} (Risk: {risk_pts} pts | ₹{risk_rupees})\n"
        f"{target_line}"
        f"{advisory_line}"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{status_line}"
    )
    send_telegram_alert(msg)

def notify_trailing_update(symbol: str, milestone: str, new_sl: float, locked_profit: float):
    msg = (
        f"🛡️ *IRON BOT: RISK SHIELD ACTIVATED*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 *Strike:* `{symbol}`\n"
        f"📈 *Milestone:* {milestone}\n"
        f"🔒 *New Trailing SL:* ₹{new_sl:.2f}\n"
        f"💰 *Guaranteed Lock:* ₹{locked_profit:.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"✨ *Trade is now 100% Risk-Free!*"
    )
    send_telegram_alert(msg)

def notify_exit(symbol: str, exit_price: float, reason: str, pnl_points: float, pnl_rupees: float):
    emoji = "🟢" if pnl_rupees >= 0 else "🔴"
    try:
        import database
        import config
        stats = database.get_today_stats()
        trades_done = stats.get("trade_count", 0)
        max_trades = getattr(config, "MAX_DAILY_TRADES", 4)
        is_locked = stats.get("is_locked", 0)
        if is_locked or trades_done >= max_trades:
            state_line = f"🔒 *System State:* DAILY LIMIT REACHED ({trades_done}/{max_trades}). Hibernating till tomorrow!"
        else:
            state_line = f"🔄 *System State:* ACTIVE ({trades_done}/{max_trades} Trades Used). Awaiting next signal!"
    except Exception:
        state_line = "🔒 *System State:* Trade closed."

    msg = (
        f"{emoji} *IRON BOT: TRADE CLOSED*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 *Strike:* `{symbol}`\n"
        f"🚪 *Exit Price:* ₹{exit_price:.2f}\n"
        f"📋 *Reason:* `{reason}`\n"
        f"📊 *Net P&L Points:* `{pnl_points:+.1f} pts`\n"
        f"💰 *Net Cash:* `{pnl_rupees:+.2f} INR`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{state_line}"
    )
    send_telegram_alert(msg)

def notify_rejection(reason: str, signal_data: dict):
    msg = (
        f"⛔ *IRON BOT: TRADE REJECTED BY ARMOR*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ *Blocked Reason:* {reason}\n"
        f"📡 *Signal:* `{signal_data.get('signal', 'N/A')}` @ Spot {signal_data.get('spot', 'N/A')}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🛡️ *Rule Enforced:* Protecting capital from trap/overtrading!"
    )
    send_telegram_alert(msg)
