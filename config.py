"""
Iron Bot Configuration & 13 Iron Armor Rules
Specially designed for Chirag Ravat
Mission: Steady Passive Income (15-20 pts) with Zero Overtrading
"""

import os
from datetime import datetime, time
from zoneinfo import ZoneInfo
from typing import Optional

# Timezone standard: Indian Standard Time (Asia/Kolkata)
# Guaranteed IST consistency on local Mac and Render Cloud (UTC containers)
IST_TIMEZONE = ZoneInfo("Asia/Kolkata")

def get_now_ist() -> datetime:
    """Returns current datetime in Indian Standard Time (Asia/Kolkata)."""
    return datetime.now(IST_TIMEZONE)

def get_today_ist() -> str:
    """Returns current date in YYYY-MM-DD in IST."""
    return get_now_ist().strftime("%Y-%m-%d")

def get_now_str_ist() -> str:
    """Returns current timestamp string in YYYY-MM-DD HH:MM:SS in IST."""
    return get_now_ist().strftime("%Y-%m-%d %H:%M:%S")

# =====================================================================
# 1. CORE OPERATIONAL MODES & CAPITAL WALLET
# =====================================================================
PAPER_TRADING_MODE = True       # 100% Virtual Ledger (₹0 Real Money at Risk)
PORT = int(os.environ.get("PORT", 5001)) # Webhook Server Port (Render passes PORT env)
HOST = "0.0.0.0"

# 30-Day Campaign & Capital Cap (Strict No-Infinite-Money Rule)
INITIAL_WALLET_CAPITAL = 100000.0        # Strict ₹1,00,000 INR Starting Seed Fund (1 Lakh)
CAMPAIGN_START_DATE = "2026-11-25"       # Fresh 30-Day Horizon Start Date
CAMPAIGN_DURATION_DAYS = 30              # 30-Day Evaluation Campaign Window
MAX_MARGIN_PER_TRADE_PCT = 0.50          # Max 50% wallet margin allocation per trade (₹50,000)
CAPITAL_PRESERVATION_FLOOR = 35000.0     # Default fallback preservation floor (-30% max drawdown)
TARGET_ROI_PCT = 20.0                    # Institutional Target: +20.0% Net ROI in 30 days

# =====================================================================
# 2. THE 13 IRON ARMOR RULES (DISCIPLINE LOCKS)
# =====================================================================
# Rule 1 & 2: Daily Trade & Loss Limits
MAX_DAILY_TRADES = 4            # Default 4 trades per day (Discipline baseline)
MAX_DAILY_LOSS_RUPEES = 2500    # Maximum daily risk limit (Hard shutdown)
DEFAULT_LOT_SIZE = 65           # 1 Lot Nifty (or 25/50 depending on contract)

def get_max_daily_trades(target_date: Optional[str] = None) -> int:
    """
    Returns dynamic daily trade quota.
    Supports date-specific overrides in DB (e.g., today only).
    If no override exists for target_date, falls back to MAX_DAILY_TRADES (4).
    """
    try:
        import database
        if not target_date:
            target_date = get_today_ist()
        override_date = database.get_config("max_trades_override_date")
        if override_date == target_date:
            val = database.get_config("max_trades_override_value")
            if val is not None:
                return int(val)
    except Exception:
        pass
    return MAX_DAILY_TRADES

# Rule 3 & 4: Time Windows
MARKET_START_TIME = time(9, 30) # No trade before 9:30 AM (avoid opening noise)
MARKET_END_TIME = time(14, 45)   # No new entries after 2:45 PM
AUTO_SQUARE_OFF_TIME = time(15, 15) # Mandatory exit at 3:15 PM

# Rule 5: European Open Blackout (1:25 PM to 1:45 PM IST)
EURO_BLACKOUT_START = time(13, 25)
EURO_BLACKOUT_END = time(13, 45)

# Rule 5B: Volatility-Gated Midday Defense Window (11:30 AM to 1:15 PM IST)
# Protects against low-liquidity theta decay chop without starving trade count.
MIDDAY_START_TIME = time(11, 30)
MIDDAY_END_TIME = time(13, 15)
MIDDAY_MIN_CANDLE_RANGE = 18.0       # Minimum 5m candle range in spot (pts) to allow midday trades
MIDDAY_SCALP_TARGET_POINTS = 11.0    # Tighter Hit & Run scalp target during active midday momentum

# Rule 6: Duplicate Webhook Protection (Idempotency)
DEBOUNCE_WINDOW_SECONDS = 60    # Reject duplicate alerts within 60s

# Rule 7: Option Premium Sweet Spot & Real Market Strike Selection
MIN_OPTION_PREMIUM = 50.0       # Ignore cheap decaying far-OTM lottery options (< ₹50)
MAX_OPTION_PREMIUM = 450.0      # Realistic ceiling for Weekly ATM/Slight ITM options (₹150 to ₹350)
DEFAULT_ASSUMED_PREMIUM = None  # Strictly disallow dummy prices. Must fetch real live exchange quote!

# Rule 8: Micro-Stop-Loss & Targets (Buchanan + Passarelli Dynamic Greeks Engine)
ENABLE_GREEKS_DYNAMIC_SL = True     # Dynamic SL derived from Delta, ATR, and Volatility
DEFAULT_SPOT_ATR = 22.0             # Default 5-minute candle ATR for Nifty 50
SPOT_NOISE_MULTIPLIER = 1.3         # 1.3x ATR breathing room to eliminate noise shakeouts
TARGET_RR_RATIO = 1.8               # 1:1.8 Risk-to-Reward Ratio (Institutional Target)
MAX_SL_PREMIUM_PCT = 0.15           # Max 15% of premium risked on any trade
MIN_SL_POINTS = 6.0                 # Minimum floor SL points
DEFAULT_MICRO_SL_POINTS = 14.0      # Fallback baseline SL (14 pts risk)
DEFAULT_TARGET_POINTS = 14.0        # Realistic Hit & Run Target (+14 pts, locks ~₹910/lot on normal days)
ENABLE_VIX_ADAPTIVE = True          # Scales SL/TP based on India VIX regime

# Rule 9: Reality-Proof 3-Stage Bracket Engine (Solves Breakeven Choke Flaw)
# Stage 1: Soft Risk Buffer (Leaves 6.0 pts breathing room for 2-tick pullback wicks, slashes risk 60%)
SOFT_SL_TRIGGER_POINTS = 7.5        # At +7.5 pts gain: Move SL to Entry - 6.0 pts
SOFT_SL_BUFFER_POINTS = 6.0         # Breathing room buffer below entry
# Stage 2: True Cost Lock (Risk-Free Ride once trade develops momentum)
COST_LOCK_TRIGGER_POINTS = 11.0     # At +11.0 pts gain: Move SL to Entry + BID_ASK_FRICTION_POINTS
# Legacy milestones preserved for backward compatibility
MILESTONE_1_TRIGGER = 11.0          # Cost lock threshold
MILESTONE_2_TRIGGER = 18.0          # Profit lock threshold (+6 pts)
MILESTONE_3_TRIGGER = 26.0          # Trend runner threshold (+14 pts)

# Rule 9B: Trend Pullback Re-entry & Momentum Tracking
TREND_PULLBACK_EMA_PERIOD = 20      # 20 EMA bounce re-entry filter

# Rule 10: Exhaustion Candle Filter (Avoid Buying the Top of a Giant Candle)
MAX_CANDLE_RANGE_POINTS = 65.0  # Scaled for Nifty 25,600+ (~3.0x ATR). If range > 65 pts, inspect wick exhaustion

# Rule 11: Theta Decay & Time Stagnation Protection (Gap #2)
STAGNATION_TIMEOUT_MINUTES = 45 # Exit if position stagnant after 45 minutes
STAGNATION_MIN_GAIN_POINTS = 5.0 # Minimum gain required to avoid stagnation exit

# Rule 12: Real Market Execution Friction & Bid-Ask Slippage (Gap #4)
BID_ASK_FRICTION_POINTS = 0.6   # Real spread between Ask buy & Bid exit

# Rule 13: Active Background Position Monitor Daemon (Gap #1)
MONITOR_POLL_INTERVAL_SECONDS = 3 # Background polling frequency

# Rule 14: Confluence Trend Runner (Unlimited Upside on Monster Days)
ENABLE_CONFLUENCE_TREND_RUNNER = True # Unlock Ratchet Trailing on Strong Confluence
RUNNER_MACRO_THRESHOLD = 30           # Required Macro Radar score (+30 for CE, -30 for PE)
RUNNER_TRAIL_OFFSET_POINTS = 15.0     # Points to trail behind peak price above +35 pts

# =====================================================================
# 3. MACRO RADAR & OPTION CHAIN CONFLUENCE (LOOSELY COUPLED)
# =====================================================================
ENABLE_MACRO_RADAR = True        # Fetches pre-market global cues & VIX
ENABLE_OI_SHIELD = True          # Tracks PCR, Call/Put Walls & Max Pain
ENFORCE_OI_HARD_FILTER = False   # False = Advisory only (Safe). True = Rejects headwind trades.

# =====================================================================
# 4. TELEGRAM NOTIFICATIONS
# =====================================================================
# Set your Telegram Bot Token & Chat ID here (or via environment variables)
TELEGRAM_BOT_TOKEN = "9174695247:AAF8_OgNYFimH7Kk-MT0OEzioRnKaCJqbOk"
TELEGRAM_CHAT_ID = "1651536670"

# =====================================================================
# 5. DHANHQ BROKER INTEGRATION (LOOSELY COUPLED)
# =====================================================================
DHAN_CLIENT_ID = os.environ.get("DHAN_CLIENT_ID", "1105133010")
DHAN_ACCESS_TOKEN = os.environ.get(
    "DHAN_ACCESS_TOKEN", 
    "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJ1c2VyUmVnaW9uIjoiUjEiLCJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzk0MzkwMzUwLCJpYXQiOjE3OTQzMDM5NTAsInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTA1MTMzMDEwIn0.3-C5AO8mcjrJi5IAFRxnN9730GFgoge9FDMjtHSzbSgj0xrugRl8psr_clBtuGZyDJN2gqiP1nLR4xUmGa25tA"
)
ENABLE_DHAN_BROKER = True

