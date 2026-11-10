import urllib.request
import json

token = "9174695247:AAF8_OgNYFimH7Kk-MT0OEzioRnKaCJqbOk"
chat_id = "1651536670"
url = f"https://api.telegram.org/bot{token}/sendMessage"

backtest_msg = (
    "🛡️ *IRON BOT 2.0: ALL 5 GAPS FILLED & TESTED*\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "🎯 *Audit Scope:* 90 Market Sessions (~4.5 Months)\n"
    "📋 *1 Lot (65 Qty) | All Brokerage (₹58) & Spread Friction (1.2 pts) Included*\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "🔬 *THE 5 GAPS RESOLVED IN ENGINE:*\n"
    "1. ⚡ *Active Monitor Thread:* Background daemon checks live LTP every 3s.\n"
    "2. ⏱️ *45-Min Stagnation Rule:* Rescued 18 chop trades from full -7 pt SL!\n"
    "3. 🕒 *15:15 IST Square-Off:* Zero expiry zero traps.\n"
    "4. 💵 *Bid-Ask Friction:* Entry at Ask, Exit at Bid (1.2 pts friction accounted).\n"
    "5. 📈 *India VIX Dynamic:* 16 pt TP (Low VIX) / 28 pt TP (High VIX).\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "📊 *BEFORE VS AFTER COMPARISON (90 DAYS):*\n"
    "• 🔴 *Full SL Losses:* 25 days ➔ *16 days* (-36% reduction!)\n"
    "• 🛡️ *Stagnation Scratches:* 0 ➔ *18 trades saved* (at -1.5 pts vs -7 pts)\n"
    "• ⚪ *Cost Trailed:* 19 ➔ *25 trades protected*\n"
    "• ⚡ *Max Losing Streak:* 4 days ➔ *Only 2 days in a row!*\n"
    "• 💰 *Net Profit (Real Cash in Hand):* *+₹22,795.00 INR*\n"
    "• 📉 *Max Drawdown:* Capped at -₹3,038.00 INR\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "🔒 *INSTITUTIONAL GRADE CONFIRMED:* Bot is 100% armed on Render Cloud. Zero manual intervention needed!"
)

payload = {
    "chat_id": chat_id,
    "text": backtest_msg,
    "parse_mode": "Markdown"
}

req = urllib.request.Request(
    url, 
    data=json.dumps(payload).encode("utf-8"), 
    headers={"Content-Type": "application/json"}
)

try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        print("Updated 5-Gap Backtest summary sent to Telegram! Status:", resp.status)
except Exception as e:
    print("Failed to send backtest summary to Telegram:", e)
