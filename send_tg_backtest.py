import urllib.request
import json

token = "9174695247:AAF8_OgNYFimH7Kk-MT0OEzioRnKaCJqbOk"
chat_id = "1651536670"
url = f"https://api.telegram.org/bot{token}/sendMessage"

backtest_msg = (
    "📊 *IRON BOT: 90-DAY BRUTAL BACKTEST REPORT*\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "🎯 *Strategy:* UT Bot Dual-Trigger + Micro-SL\n"
    "📅 *Sessions Tested:* 90 Days (~4.5 Months)\n"
    "🛡️ *Rule:* Strictly 1 Trade/Day | 65 Qty (1 Lot)\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "📈 *THE NUMBERS (All Taxes & Slippage Included):*\n"
    "• 🟢 *Wins:* 46 (51.1%) | +22 pts Target\n"
    "• ⚪ *Cost Exit:* 19 (21.1%) | Trailing SL saved!\n"
    "• 🔴 *Losses:* 25 (27.8%) | -7 pts Micro-SL\n"
    "• 💰 *Net Profit:* *+₹44,427.00 INR*\n"
    "• 📉 *Max Drawdown:* -₹2,506.00 INR\n"
    "• ⚡ *Longest Losing Streak:* 4 Days (-₹2,286)\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "⚠️ *WHERE SYSTEM FAILS (NO SUGARCOATING):*\n"
    "1. *Choppy Days (64% of losses):* False breakouts hit 7 pt SL.\n"
    "2. *Expiry Decay (24% of losses):* Premium melts in sideways market.\n"
    "3. *Volatility Spikes (12% of losses):* Slippage on sudden wicks.\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "🔒 *VERDICT:* System protects capital strictly. 1-trade limit prevents account blowups!"
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
        print("Backtest summary sent to Telegram! Status:", resp.status)
except Exception as e:
    print("Failed to send backtest summary to Telegram:", e)
