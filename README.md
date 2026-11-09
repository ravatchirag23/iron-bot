# 🤖 IRON BOT — User Manual & Operations Guide
**Mission:** ₹20,000 – ₹25,000 Monthly Passive Income (15-20 pts) with Zero Overtrading  
**Mode:** 100% Virtual Paper Trading Ledger (₹0 Real Money at Risk)  
**Author:** Pair programmed for Chirag Ravat

---

## 📁 Directory Structure
```
iron_bot/
├── config.py              # The 13 Iron Armor Rules & Parameters
├── database.py            # SQLite Persistent Ledger (trades.db)
├── risk_manager.py        # 13 Armor Rules Validator & ATM Strike Picker
├── paper_engine.py        # Virtual Execution & Tiered Trailing Engine
├── telegram_notifier.py   # Telegram Alerts & Console Notifier
├── webhook_server.py      # Zero-Dependency Gateway & Live Web Dashboard
├── backtester.py          # 60-Day Historical Backtest Simulator
├── simulate_test.py       # End-to-End 13-Rule Verification Test
└── trades.db              # SQLite Database (Auto-created)
```

---

## 🚀 Quick Start (How to Run on Mac)

### 1. Run Verification Test:
Verify that all 13 rules are working:
```bash
python3 simulate_test.py
```

### 2. Run Historical Backtest:
Run 60-day mathematical expectancy test:
```bash
python3 backtester.py
```

### 3. Start Live Webhook Server & Dashboard:
```bash
python3 webhook_server.py
```
Open your browser and visit:
👉 **`http://localhost:5001/status`**

You will see:
* **Live System Status:** (Armed / Locked)
* **Today's Trade Count:** (0 / 1)
* **Today's Net P&L:** (Live green/red)
* **Active Position Card:** (Current Strike, Entry, SL, Target)
* **Audit Trail Table:** (Every past paper trade with timestamps and reasons)

---

## 📡 GoCharting Webhook Setup (How to Connect)

In your GoCharting chart with `UT Bot Master Suite (Visible S/R Zones + Options Engine)`:

1. Click **Alerts** on the chart (top menu).
2. Create New Alert on study: **UT Bot Master Suite**.
3. Condition: **`alertcondition: UT Long / CE Entry`** (or `UT Short / PE Entry`).
4. Trigger: **`Once Per Bar Close`** (Very important: only on candle close!).
5. Webhook URL:
   * Local: `http://localhost:5001/webhook` (or via Cloudflare/ngrok).
6. Message (JSON Payload):
```json
{
  "signal": "BUY_CE",
  "spot": {{close}},
  "candle_low": {{low}},
  "candle_range": 15
}
```

---

## 🛡️ The 13 Iron Armor Rules Enforced in Code
1. **Max 1 Trade/Day:** Rejects any second trade automatically.
2. **Max Loss Cap:** Hard shutdown if daily loss reaches -₹600.
3. **Micro-SL:** Capped at 6–8 points max (Risk: ~₹400–₹500 on 65 qty).
4. **Sweet Spot ATM Picker:** Rounds spot price to nearest 50 ATM strike.
5. **No Cheap OTM:** Ignores options < ₹45.
6. **No High-Delta ITM:** Ignores options > ₹75.
7. **Exhaustion Candle Filter:** Skips trades if signal candle > 45 index points.
8. **Euro-Open Blackout:** Blocks all entries between 13:25 and 13:45 IST.
9. **Duplicate Debounce:** Rejects duplicate webhooks within 60 seconds.
10. **Tiered Milestone Trailing:**
    * At +12 pts: Moves SL to Cost (Risk-Free!).
    * At +22 pts: Target reached or locks +10 pts.
    * At +35 pts: Locks +22 pts.
11. **Time Window:** No entry before 9:30 AM or after 2:45 PM.
12. **Auto Square-Off:** Closes all positions by 3:15 PM.
13. **100% Virtual Ledger:** Runs in Paper Mode; real broker wallet remains frozen!
