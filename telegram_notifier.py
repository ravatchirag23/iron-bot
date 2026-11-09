"""
Telegram Notification Engine for Iron Bot
Sends instant alerts to Chirag's phone when trades trigger, trail, or close.
"""

import urllib.request
import urllib.parse
import json
import logging
from typing import Optional
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger("IronBot.Telegram")

def send_telegram_alert(message: str) -> bool:
    """Send formatted markdown alert to Telegram. Falls back gracefully if no token configured."""
    print(f"\n📢 [IRON BOT ALERT]\n{message}\n" + "-"*45)
    
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return True # Handled in console
        
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status == 200
    except Exception as e:
        logger.error(f"Failed to send Telegram message: {e}")
        return False

def notify_entry(symbol: str, signal_type: str, entry_price: float, sl_price: float, target_price: float, quantity: int):
    risk_pts = round(entry_price - sl_price if "BUY" in signal_type or "CE" in signal_type else sl_price - entry_price, 1)
    risk_rupees = round(risk_pts * quantity, 2)
    
    msg = (
        f"🤖 *IRON BOT: NEW TRADE TRIGGERED*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 *Signal:* `{signal_type}`\n"
        f"🎯 *Strike:* `{symbol}` (1 Lot: {quantity} Qty)\n"
        f"💵 *Entry Price:* ₹{entry_price:.2f}\n"
        f"🛑 *Micro-SL:* ₹{sl_price:.2f} (Risk: {risk_pts} pts | ₹{risk_rupees})\n"
        f"🚀 *Target:* ₹{target_price:.2f} (+{target_price - entry_price:.1f} pts)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔒 *Status:* 1-Trade Rule Active. No manual action needed!"
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
    msg = (
        f"{emoji} *IRON BOT: TRADE CLOSED*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 *Strike:* `{symbol}`\n"
        f"🚪 *Exit Price:* ₹{exit_price:.2f}\n"
        f"📋 *Reason:* `{reason}`\n"
        f"📊 *Net P&L Points:* `{pnl_points:+.1f} pts`\n"
        f"💰 *Net Cash:* `{pnl_rupees:+.2f} INR`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔒 *System State:* DAILY LIMIT REACHED. Hibernating till tomorrow!"
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
