import urllib.request
import json

token = "9174695247:AAF8_OgNYFimH7Kk-MT0OEzioRnKaCJqbOk"
chat_id = "1651536670"
url = f"https://api.telegram.org/bot{token}/sendMessage"

backtest_msg = (
    "📊 *IRON BOT 2.0: 4-MONTH DEEP AUDIT REPORT*\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "🎯 *Audit Scope:* 88 Real Sessions (July - Nov 2026)\n"
    "📋 *1 Lot (65 Qty) | Real Spread Friction + ₹5,104 Taxes Paid*\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "🏆 *THE 4-MONTH AUDIT NUMBERS:*\n"
    "• 🟢 *Clean Wins:* 15 (17.0%) | +16 to +22 pts Target\n"
    "• 🌊 *Trend Runner Wins:* 16 (18.2%) | Surfed +35 to +85 pts!\n"
    "• ⚪ *Cost Trailed (₹0 Risk):* 27 (30.7%) | Trailed to entry\n"
    "• 🛡️ *45-Min Scratches:* 21 (23.9%) | Saved from full SL at -1.5 pts\n"
    "• 🔴 *Hard SL Losses:* 9 (10.2%) | Capped at -6 to -9 pts\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "💰 *NET IN-HAND PROFIT:* *+₹63,528.24 INR*\n"
    "📈 *Monthly Average:* *+₹15,882.06 / Month*\n"
    "🔥 *ROI on ₹15k Margin:* *+423.5% across 4 Months*\n"
    "📉 *Max Drawdown:* -₹2,775.50 INR (18.5% of capital)\n"
    "⚡ *Longest Losing Streak:* Only 2 Days in a row!\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "🔒 *INSTITUTIONAL GRADE CONFIRMED:* Zero overtrading, zero revenge trading, zero manual interventions!"
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
        print("4-Month Deep Backtest sent to Telegram! Status:", resp.status)
except Exception as e:
    print("Failed to send deep backtest to Telegram:", e)
