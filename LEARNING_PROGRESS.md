# Chirag's Learning & Engineering Progress

**Student:** Chirag Ravat (5 yrs L2 Production Support: Oracle SQL, RHEL, AppDynamics, ServiceNow)  
**Mission:** Zero → Senior AI Architect in 12 Months  
**Current Phase:** Phase 1 (Python Core, CLI Automation, Trading Engineering)

---

## 📅 Session Log: 12-Nov-2026

### 1. Options Greeks & Risk Engineering (Deep Dive)
- **Buchanan Greeks Lecture:** Black-Scholes PDE, analytical Greeks ($\Delta, \Gamma, \Theta, \mathcal{V}$), Taylor series second-order price expansion ($\Delta F \approx \Delta \delta S + \frac{1}{2}\Gamma(\delta S)^2 + \Theta \delta t$).
- **Dan Passarelli ("Trading Option Greeks"):** The Underlying Anchor rule, OTM vs ATM risk profiles, position sizing over choking stops, multi-target trailing.
- **Root Cause Analysis (Trade #8):** Mathematically proved why fixed 7-pt SL on ATM ₹199 option failed in 4 minutes (allowed only 14 Nifty spot pts, inside normal 5m ATR of 22 pts).

### 2. Engineering Upgrades Deployed to Production (AWS EC2 Mumbai)
- **Daily 8:00 AM Pre-Market Automated Handshake:**
  - Migrated handshake daemon from legacy Dhan token to **Angel One SmartAPI TOTP auto-handshake**.
  - Installed missing dependencies (`logzero`, `websocket-client`, `smartapi-python`, `pyotp`) in EC2 production venv.
  - Automatically dispatches full pre-market status briefing to Telegram daily between 08:00 - 08:30 AM without manual intervention.
- **Dynamic Greeks SL/TP Engine (`risk_manager.py`):**
  - Spot breathing room: $\Delta S_{\text{stop}} = 1.3 \times \text{ATR} \times \sqrt{\text{VIX} / 15.0}$ (~30.1 pts).
  - Dynamic SL: $\text{SL}_{\text{pts}} = |\Delta| \times \Delta S_{\text{stop}} + \text{Half-Spread}$ (min 6 pts, max 15% premium).
  - Dynamic Target: $1.8 \times \text{SL}$ (Institutional 1:1.8 RRR + Gamma booster).
- **Dynamic Trailing Engine (`paper_engine.py`):**
  - Milestone 1: Cost lock at $1.0 \times \text{SL}$ gain.
  - Milestone 2: Lock $+0.75 \times \text{SL}$ profit at $1.5 \times \text{SL}$ gain.
  - Milestone 3: Lock $+1.5 \times \text{SL}$ profit at $2.2 \times \text{SL}$ gain.
- **Max Trades Per Day:** Upgraded from 1 to 4 trades per day (`MAX_DAILY_TRADES = 4`, `MAX_DAILY_LOSS_RUPEES = 2500`).
- **Telegram Notifier:** Live dynamic counters (`Trade X/4 Active`, `ACTIVE (X/4 Trades Used)`).
- **Angel One SmartAPI Gateway (`angel_connector.py`):**
  - Live TOTP auto-handshake verified on both Mac and AWS EC2 (Client: `C51977841`, Chirag Ravat).
  - Webhook Dashboard card integrated showing live connection status.
  - Canary Virtual Lock (`PAPER_TRADING_MODE = True`) guarantees ₹0 real order placement while logging simulated orders.
- **Complete Test & Git Verification:**
  - 13/13 comprehensive multi-trade integration tests passed (`tests/test_full_system_suite.py`).
  - 5/5 core unit tests passed (`tests/test_iron_bot.py`).
  - GitHub repository (`main`) and AWS EC2 synced clean at commit `41b296c`.
  - Service `iron-bot.service` restarted and running cleanly on EC2 (PID 11593).

---

## 📅 Session Log: 13-Nov-2026 (Live Market Production Execution)

### 1. Live Trading Audit (Full Day Autonomous Execution)
- **Total Trades Taken:** 3 / 4 Trades
- **Session Outcome:**
  - **Trade #51 (11:00 AM):** `NIFTY25200PE` @ ₹156.30 $\rightarrow$ Exit: ₹141.10 (`MICRO_SL_HIT`, -15.2 pts / -₹988.00).
  - **Trade #52 (01:45 PM):** `NIFTY25100PE` @ ₹131.60 $\rightarrow$ Milestone 2 Profit Lock $\rightarrow$ Exit: ₹138.50 (`TRAILING_SL_HIT`, +6.9 pts / +₹448.50 🟢).
  - **Trade #53 (02:32 PM):** `NIFTY25100CE` @ ₹149.40 $\rightarrow$ Peak ₹155.00 $\rightarrow$ Exit: ₹133.60 (`MICRO_SL_HIT`, -15.8 pts / -₹1027.00).
- **Net Daily P&L:** -₹1,566.50 (Paper Virtual Mode, ₹0 Real Risk).
- **Armor Performance:**
  - European Open Whiplash filter (Rule 5) successfully rejected 4 false trap signals between 13:25 and 13:45 IST.
  - Buchanan Greeks Dynamic SL (~15.2 - 15.8 pts) contained risk within strict institutional boundaries.
  - Daily Loss Cap (-₹2,500) respected with ₹933.50 safety margin remaining.

### 2. Critical Production Bug Audit & Hotfix (Post-Market)
- **Problem Statement:** Trade #51 (`25200PE` @ ₹156.30) and Trade #53 (`25100CE` @ ₹149.40) prematurely stopped out despite Nifty moving 25-35 points in favorable direction.
- **RCA Findings (4 Root Causes):**
  1. **Floating ATM Strike Leak (`webhook_server.py` & `option_chain_shield.py`):** Position monitor called `get_real_option_quote(spot, is_call)`, which dynamically calculated `atm_strike = round(spot / 50.0) * 50`. When Nifty dropped, ATM shifted from `25200PE` to `25150PE` (priced at ₹142.35), comparing it against entry ₹156.30, while held `25200PE` was trading in profit at ₹168.80! Falsely triggered `MICRO_SL_HIT`.
  2. **Option Shorting Typo (`risk_manager.py` line 226):** Parameter dictionary returned `"signal_type": "SELL_PE"` instead of `"BUY_PE"`, which would route as an option shorting order to broker connectors.
  3. **Confluence Substring Trap (`option_chain_shield.py` line 288):** `is_call = "CE" in signal_type.upper() or "BUY" in signal_type.upper()` caused `"BUY_PE"` to evaluate `is_call = True`, testing Put setups against Call resistance walls.
  4. **Scanner ATM Direction Check (`native_scanner.py` line 274):** Used `"BUY" in signal_type` instead of checking for `"CE"`.
- **Engineering Hotfixes Implemented & Deployed:**
  - Added full strike lookup table (`strikes_map`) to `fetch_live_nse_option_chain()` and `get_simulated_live_oi_structure()`.
  - Created `get_option_quote_by_strike(target_strike, is_call, spot_price)` to guarantee exact strike-level price tracking.
  - Extracted held strike via regex (`r'NIFTY(\d+)'`) in position monitor daemon.
  - Fixed `signal_type` to `"BUY_PE"`.
  - Fixed confluence and scanner direction checks.
- **Verification & Deployment:**
  - 13/13 full system tests passed (`tests/test_full_system_suite.py`).
  - 5/5 unit tests passed (`tests/test_iron_bot.py`).
  - GitHub `main` updated at commit `8838435`.
  - AWS EC2 production deployed and `iron-bot.service` active (PID 13255, port 5001).

---

## 📅 Session Log: 14-Nov-2026 (Institutional Battlestation Dashboard & UI Architecture)

### 1. Root Cause Analysis & Macro Data Accuracy Hotfix
- **Yahoo Finance Percentage Formula:** Identified that `(price - chartPreviousClose) / chartPreviousClose` produced inaccurate session percentages because `chartPreviousClose` refers to the start of multi-day range rather than previous session close. Extracted official `meta["regularMarketChangePercent"]` directly.
- **GIFT Nifty 24/7 Uptime Engine:** Fixed weekend `--` issue where `/api/deep-intraday-graph` returned empty array `[]`. Switched primary ingestion to NSE IX Derivatives Settlement API (`/api/market-rate?type=derivatives`) with SQLite database fallback.
- **Cross-Checked Tickers Verified Live:** GIFT Nifty `25,266.0 (+0.38%)`, Dow Jones `52,347.6 (+0.10%)`, Nasdaq `25,136.3 (-0.91%)`, Brent Crude `$96.08 (+0.27%)`, USD/INR `₹94.23 (+0.20%)`, India VIX `15.50 (-6.75%)`, Nifty 50 Spot `25,144.30 (-0.61%)`.

### 2. Battlestation UI Upgrades Deployed to AWS EC2
- **TradingView Lightweight Candlestick Radar:**
  - Integrated 5-minute real candlestick stream via `/api/candles` with zero third-party license restrictions.
  - Plotted 20 EMA trend confluence line over candles.
  - Interactive crosshair OHLC legend and multi-symbol toggle (`NIFTY 50`, `BANK NIFTY`, `S&P 500`).
- **Interactive Paper Testing Sandbox:**
  - Added live order trigger controls (`Test BUY 25200 CE`, `Test BUY 25100 PE`, `Reset Quota 0/4`).
  - Added active position testing controls (`+10 Pts Up`, `-10 Pts Down`, `Emergency Square-Off`).
- **CNBC Real-Time News Ticker Ribbon:**
  - Implemented decoupled zero-latency background worker parsing CNBC Market Insider RSS feed.
  - Smooth CSS marquee ticker displaying top global financial headlines with 0% impact on trading execution latency.
- **Visual Option Chain Depth Bar:**
  - Dynamic Put Support Floor (25,000) vs Call Resistance Ceiling (25,500) visual ratio bar with PCR bias indicator.
- **Verification & Deployment:**
  - 18/18 unit & full integration tests passed.
  - Git commit `130bd60` pushed to `main` and pulled to AWS EC2 Mumbai.
  - Systemd service `iron-bot.service` active and verified live on browser.

---

## 🎯 Next Steps
- Resume Track A (Python Core & Automation) daily drills.
- Review weekend pre-market briefing and scheduled 8:00 AM TOTP auto-handshake.
