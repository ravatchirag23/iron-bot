"""
Database & Persistent Ledger Module for Iron Bot
Stores all paper trades, audit logs, daily stats, and debounce locks in SQLite.
"""

import sqlite3
import os
from datetime import datetime, date
from typing import Optional, Dict, Any, List
from contextlib import contextmanager
import config

DB_PATH = os.path.join(os.path.dirname(__file__), "trades.db")

def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 15000;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn

@contextmanager
def db_session():
    """Thread-safe context manager guaranteeing safe commits, rollbacks, and connection closures."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    
    # 1. Trades table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        signal_type TEXT NOT NULL,       -- BUY_CE or SELL_PE
        symbol TEXT NOT NULL,            -- e.g. NIFTY23800CE
        spot_price REAL NOT NULL,
        entry_price REAL NOT NULL,
        sl_price REAL NOT NULL,
        initial_sl_price REAL,           -- Baseline entry SL before trailing updates
        target_price REAL NOT NULL,
        quantity INTEGER NOT NULL,
        exit_price REAL,
        exit_time TEXT,
        exit_reason TEXT,                -- TARGET_HIT, SL_HIT, TIME_EXIT, TRAILING_SL_HIT
        pnl_points REAL DEFAULT 0.0,
        pnl_rupees REAL DEFAULT 0.0,
        status TEXT NOT NULL,            -- ACTIVE, CLOSED
        is_paper INTEGER DEFAULT 1
    );
    """)

    # 2. Daily summary tracker
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS daily_tracker (
        trade_date TEXT PRIMARY KEY,
        trade_count INTEGER DEFAULT 0,
        total_pnl_rupees REAL DEFAULT 0.0,
        is_locked INTEGER DEFAULT 0,
        lock_reason TEXT
    );
    """)

    # 3. Audit & debounce logs
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        event_type TEXT NOT NULL,
        details TEXT NOT NULL
    );
    """)

    # 4. System config table (dynamic tokens & broker settings)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS system_config (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """)

    # Safe Schema Migration for Trend Runner (is_runner column)
    try:
        cursor.execute("ALTER TABLE trades ADD COLUMN is_runner INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass # Column already exists

    # Safe Schema Migration for Initial SL Price tracking
    try:
        cursor.execute("ALTER TABLE trades ADD COLUMN initial_sl_price REAL")
    except sqlite3.OperationalError:
        pass # Column already exists
    
    # High-Performance Indexes for 0ms Active Trade & Ledger Query Execution
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(trade_date);")
    
    conn.commit()
    conn.close()

def log_event(event_type: str, details: str):
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO audit_logs (timestamp, event_type, details) VALUES (?, ?, ?)",
            (config.get_now_str_ist(), event_type, details)
        )

def is_duplicate_alert(signal_type: str, debounce_seconds: int = 60) -> bool:
    """Check if same signal arrived within debounce window to prevent duplicate executions."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT timestamp FROM audit_logs 
            WHERE event_type = ? 
            ORDER BY id DESC LIMIT 1
        """, (f"SIGNAL_RECEIVED_{signal_type}",))
        row = cursor.fetchone()
        if not row:
            return False
            
        last_time = datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
        now_naive = config.get_now_ist().replace(tzinfo=None)
        elapsed = (now_naive - last_time).total_seconds()
        return elapsed < debounce_seconds
    finally:
        conn.close()

def get_today_stats(today_str: Optional[str] = None) -> Dict[str, Any]:
    if not today_str:
        today_str = config.get_today_ist()
        
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM daily_tracker WHERE trade_date = ?", (today_str,))
        row = cursor.fetchone()
        
        if not row:
            cursor.execute("INSERT OR IGNORE INTO daily_tracker (trade_date, trade_count, total_pnl_rupees, is_locked) VALUES (?, 0, 0.0, 0)", (today_str,))
            conn.commit()
            stats = {"trade_date": today_str, "trade_count": 0, "total_pnl_rupees": 0.0, "is_locked": 0, "lock_reason": ""}
        else:
            stats = dict(row)
        return stats
    finally:
        conn.close()

def record_trade_entry(trade_data: Dict[str, Any]) -> int:
    today_str = config.get_today_ist()
    now_str = config.get_now_str_ist()
    
    is_runner = 1 if trade_data.get("is_runner", False) else 0
    initial_sl = float(trade_data.get("initial_sl_price") or trade_data["sl_price"])

    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO trades (
                trade_date, timestamp, signal_type, symbol, spot_price,
                entry_price, sl_price, initial_sl_price, target_price, quantity, status, is_paper, is_runner
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', 1, ?)
        """, (
            today_str, now_str, trade_data["signal_type"], trade_data["symbol"],
            trade_data["spot_price"], trade_data["entry_price"], trade_data["sl_price"],
            initial_sl, trade_data["target_price"], trade_data["quantity"], is_runner
        ))
        
        trade_id = cursor.lastrowid
        
        # Increment daily count
        cursor.execute("""
            INSERT INTO daily_tracker (trade_date, trade_count, total_pnl_rupees, is_locked)
            VALUES (?, 1, 0.0, 0)
            ON CONFLICT(trade_date) DO UPDATE SET trade_count = trade_count + 1
        """, (today_str,))
    
    log_event(f"TRADE_ENTERED_{trade_data['signal_type']}", f"Trade #{trade_id} on {trade_data['symbol']} @ {trade_data['entry_price']}")
    return trade_id

def record_trade_exit(trade_id: int, exit_price: float, exit_reason: str, pnl_points: float, pnl_rupees: float):
    now_str = config.get_now_str_ist()
    today_str = config.get_today_ist()
    
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE trades SET
                exit_price = ?,
                exit_time = ?,
                exit_reason = ?,
                pnl_points = ?,
                pnl_rupees = ?,
                status = 'CLOSED'
            WHERE id = ?
        """, (exit_price, now_str, exit_reason, pnl_points, pnl_rupees, trade_id))
        
        # Update daily total PnL
        cursor.execute("""
            UPDATE daily_tracker SET
                total_pnl_rupees = total_pnl_rupees + ?
            WHERE trade_date = ?
        """, (pnl_rupees, today_str))
    
    log_event("TRADE_EXITED", f"Trade #{trade_id} closed @ {exit_price} | PnL: {pnl_points:.1f} pts (₹{pnl_rupees:.2f}) | Reason: {exit_reason}")

def lock_system_today(reason: str):
    today_str = config.get_today_ist()
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE daily_tracker SET is_locked = 1, lock_reason = ? WHERE trade_date = ?
        """, (reason, today_str))
    log_event("SYSTEM_LOCKED", f"System locked for {today_str}: {reason}")

def get_active_trade() -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trades WHERE status = 'ACTIVE' ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def get_all_trades(limit: int = 20) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def get_config(key: str, default: Optional[str] = None) -> Optional[str]:
    """Retrieves dynamic config value from system_config."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM system_config WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row["value"] if row else default
    finally:
        conn.close()

def set_config(key: str, value: str):
    """Saves or updates dynamic config value in system_config."""
    now_str = config.get_now_str_ist()
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO system_config (key, value, updated_at) 
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """, (key, value, now_str))
    log_event("CONFIG_UPDATED", f"Key '{key}' updated dynamically")

def reset_daily_limits(today_str: Optional[str] = None):
    """Clears daily_tracker and closes any active trades for clean testing."""
    if not today_str:
        today_str = config.get_today_ist()
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM daily_tracker WHERE trade_date = ?", (today_str,))
        cursor.execute("UPDATE trades SET status = 'CLOSED' WHERE status = 'ACTIVE'")

def get_wallet_stats() -> Dict[str, Any]:
    """
    Computes strict ₹50,000 INR Wallet & 30-Day Campaign Performance Metrics.
    Tracks starting seed capital, current equity, available margin, ROI %, and circuit breakers.
    """
    start_date_str = get_config("campaign_start_date", getattr(config, "CAMPAIGN_START_DATE", "2026-11-15"))
    initial_capital = float(get_config("initial_wallet_capital", str(getattr(config, "INITIAL_WALLET_CAPITAL", 50000.0))))
    duration_days = int(getattr(config, "CAMPAIGN_DURATION_DAYS", 30))
    target_roi_pct = float(getattr(config, "TARGET_ROI_PCT", 20.0))
    preservation_floor = float(getattr(config, "CAPITAL_PRESERVATION_FLOOR", 35000.0))
    
    # Calculate Day N of 30
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    except Exception:
        start_date = config.get_now_ist().date()
        start_date_str = start_date.strftime("%Y-%m-%d")

    today_date = config.get_now_ist().date()
    days_elapsed = (today_date - start_date).days + 1
    day_number = max(1, min(days_elapsed, duration_days))
    days_remaining = max(0, duration_days - day_number + 1)
    
    conn = get_connection()
    cursor = conn.cursor()
    
    # 1. Closed trades within current campaign window (trade_date >= start_date)
    # Exclude dummy test dates (e.g. 2026-11-96/97/98 from unit tests)
    cursor.execute("""
        SELECT 
            COUNT(*),
            COALESCE(SUM(pnl_rupees), 0.0),
            COALESCE(SUM(pnl_points), 0.0),
            COALESCE(MAX(pnl_rupees), 0.0),
            COALESCE(MIN(pnl_rupees), 0.0)
        FROM trades 
        WHERE status = 'CLOSED' 
          AND trade_date >= ?
          AND trade_date NOT LIKE '%-9_'
    """, (start_date_str,))
    agg = cursor.fetchone()
    
    trade_count = agg[0] or 0
    realized_pnl = round(float(agg[1]), 2)
    realized_points = round(float(agg[2]), 1)
    best_trade = round(float(agg[3]), 2)
    worst_trade = round(float(agg[4]), 2)
    
    # 2. Win / Loss stats for campaign
    cursor.execute("""
        SELECT 
            COUNT(CASE WHEN pnl_rupees > 0 THEN 1 END),
            COUNT(CASE WHEN pnl_rupees < 0 THEN 1 END)
        FROM trades 
        WHERE status = 'CLOSED' 
          AND trade_date >= ?
          AND trade_date NOT LIKE '%-9_'
    """, (start_date_str,))
    w_row = cursor.fetchone()
    wins = w_row[0] or 0
    losses = w_row[1] or 0
    win_rate = round((wins / trade_count * 100.0), 1) if trade_count > 0 else 0.0
    
    # 3. Active trade margin lock
    cursor.execute("SELECT * FROM trades WHERE status = 'ACTIVE' ORDER BY id DESC LIMIT 1")
    active = cursor.fetchone()
    margin_used = 0.0
    active_trade_info = None
    if active:
        active_trade_info = dict(active)
        entry_px = float(active_trade_info.get("entry_price", 0.0))
        q = int(active_trade_info.get("quantity", config.DEFAULT_LOT_SIZE))
        margin_used = round(entry_px * q, 2)
        
    conn.close()
    
    safe_initial_capital = initial_capital if initial_capital > 0 else 50000.0
    safe_duration_days = max(1, duration_days)
    
    current_balance = round(initial_capital + realized_pnl, 2)
    available_margin = max(0.0, round(current_balance - margin_used, 2))
    roi_pct = round((realized_pnl / safe_initial_capital) * 100.0, 2)
    
    target_profit = round(safe_initial_capital * (target_roi_pct / 100.0), 2)
    target_balance = round(safe_initial_capital + target_profit, 2)
    progress_to_target = 0.0
    if target_profit > 0 and realized_pnl > 0:
        progress_to_target = min(100.0, round((realized_pnl / target_profit) * 100.0, 1))

    is_preservation_triggered = current_balance <= preservation_floor
    max_safe_drawdown = round(current_balance - preservation_floor, 2)
    
    # Timeline percentage (e.g. Day 1 of 30 = 3.3%)
    timeline_pct = min(100, max(0, int((day_number / safe_duration_days) * 100)))

    return {
        "campaign_name": "30-Day ₹50K Alpha Campaign",
        "start_date": start_date_str,
        "day_number": day_number,
        "total_days": duration_days,
        "days_remaining": days_remaining,
        "timeline_pct": timeline_pct,
        "initial_capital": initial_capital,
        "current_balance": current_balance,
        "realized_pnl": realized_pnl,
        "realized_points": realized_points,
        "roi_pct": roi_pct,
        "margin_used": margin_used,
        "available_margin": available_margin,
        "trade_count": trade_count,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "best_trade": best_trade,
        "worst_trade": worst_trade,
        "target_roi_pct": target_roi_pct,
        "target_profit": target_profit,
        "target_balance": target_balance,
        "progress_to_target": progress_to_target,
        "preservation_floor": preservation_floor,
        "max_safe_drawdown": max_safe_drawdown,
        "is_preservation_triggered": is_preservation_triggered
    }

def init_campaign_wallet(initial_capital: float = 50000.0, start_date: Optional[str] = None):
    """Initializes or resets the 30-Day ₹50,000 Campaign starting ledger."""
    if not start_date:
        start_date = config.get_today_ist()
    set_config("initial_wallet_capital", str(float(initial_capital)))
    set_config("campaign_start_date", start_date)
    log_event("CAMPAIGN_INITIALIZED", f"30-Day Campaign started on {start_date} with ₹{initial_capital:,.2f} wallet cap")

# Initialize tables when module is imported
init_db()
