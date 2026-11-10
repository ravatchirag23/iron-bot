"""
Iron Bot Configuration & 13 Iron Armor Rules
Specially designed for Chirag Ravat
Mission: Steady Passive Income (15-20 pts) with Zero Overtrading
"""

import os
from datetime import datetime, time
from zoneinfo import ZoneInfo

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
# 1. CORE OPERATIONAL MODES
# =====================================================================
PAPER_TRADING_MODE = True       # 100% Virtual Ledger (₹0 Real Money at Risk)
PORT = int(os.environ.get("PORT", 5001)) # Webhook Server Port (Render passes PORT env)
HOST = "0.0.0.0"

# =====================================================================
# 2. THE 13 IRON ARMOR RULES (DISCIPLINE LOCKS)
# =====================================================================
# Rule 1 & 2: Daily Trade & Loss Limits
MAX_DAILY_TRADES = 1            # Strictly 1 trade per day in Phase 1
MAX_DAILY_LOSS_RUPEES = 600     # Maximum daily risk limit (Hard shutdown)
DEFAULT_LOT_SIZE = 65           # 1 Lot Nifty (or 25/50 depending on contract)

# Rule 3 & 4: Time Windows
MARKET_START_TIME = time(9, 30) # No trade before 9:30 AM (avoid opening noise)
MARKET_END_TIME = time(14, 45)   # No new entries after 2:45 PM
AUTO_SQUARE_OFF_TIME = time(15, 15) # Mandatory exit at 3:15 PM

# Rule 5: European Open Blackout (1:25 PM to 1:45 PM IST)
EURO_BLACKOUT_START = time(13, 25)
EURO_BLACKOUT_END = time(13, 45)

# Rule 6: Duplicate Webhook Protection (Idempotency)
DEBOUNCE_WINDOW_SECONDS = 60    # Reject duplicate alerts within 60s

# Rule 7: Option Premium Sweet Spot & Strike Selection
MIN_OPTION_PREMIUM = 45.0       # Ignore cheap decaying OTM options (< ₹45)
MAX_OPTION_PREMIUM = 75.0       # Ignore expensive high-delta options (> ₹75)
DEFAULT_ASSUMED_PREMIUM = 58.0  # Typical ATM entry price when not provided

# Rule 8: Micro-Stop-Loss & Targets (Baseline Defaults)
DEFAULT_MICRO_SL_POINTS = 7.0   # Max 7 points stop loss (~₹450 risk on 65 qty)
DEFAULT_TARGET_POINTS = 22.0    # 22 points target (~₹1,430 profit on 65 qty)
ENABLE_VIX_ADAPTIVE = True      # Scales SL/TP based on India VIX regime

# Rule 9: Tiered Milestone Trailing Protection
MILESTONE_1_TRIGGER = 12.0      # At +12 pts: Move SL to Entry (Cost / Breakeven)
MILESTONE_2_TRIGGER = 22.0      # At +22 pts: Lock minimum +10 pts profit
MILESTONE_3_TRIGGER = 35.0      # At +35 pts: Lock minimum +22 pts profit

# Rule 10: Exhaustion Candle Filter (Avoid Buying the Top of a Giant Candle)
MAX_CANDLE_RANGE_POINTS = 45.0  # If signal candle > 45 index points, skip entry

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
