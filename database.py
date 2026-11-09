"""
Database & Persistent Ledger Module for Iron Bot
Stores all paper trades, audit logs, daily stats, and debounce locks in SQLite.
"""

import sqlite3
import os
from datetime import datetime, date
from typing import Optional, Dict, Any, List
import config

DB_PATH = os.path.join(os.path.dirname(__file__), "trades.db")

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

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
    
    conn.commit()
    conn.close()

def log_event(event_type: str, details: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO audit_logs (timestamp, event_type, details) VALUES (?, ?, ?)",
        (config.get_now_str_ist(), event_type, details)
    )
    conn.commit()
    conn.close()

def is_duplicate_alert(signal_type: str, debounce_seconds: int = 60) -> bool:
    """Check if same signal arrived within debounce window to prevent duplicate executions."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT timestamp FROM audit_logs 
        WHERE event_type = ? 
        ORDER BY id DESC LIMIT 1
    """, (f"SIGNAL_RECEIVED_{signal_type}",))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return False
        
    last_time = datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
    now_naive = config.get_now_ist().replace(tzinfo=None)
    elapsed = (now_naive - last_time).total_seconds()
    return elapsed < debounce_seconds

def get_today_stats(today_str: Optional[str] = None) -> Dict[str, Any]:
    if not today_str:
        today_str = config.get_today_ist()
        
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM daily_tracker WHERE trade_date = ?", (today_str,))
    row = cursor.fetchone()
    
    if not row:
        cursor.execute("INSERT OR IGNORE INTO daily_tracker (trade_date, trade_count, total_pnl_rupees, is_locked) VALUES (?, 0, 0.0, 0)", (today_str,))
        conn.commit()
        stats = {"trade_date": today_str, "trade_count": 0, "total_pnl_rupees": 0.0, "is_locked": 0, "lock_reason": ""}
    else:
        stats = dict(row)
        
    conn.close()
    return stats

def record_trade_entry(trade_data: Dict[str, Any]) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    
    today_str = config.get_today_ist()
    now_str = config.get_now_str_ist()
    
    cursor.execute("""
        INSERT INTO trades (
            trade_date, timestamp, signal_type, symbol, spot_price,
            entry_price, sl_price, target_price, quantity, status, is_paper
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', 1)
    """, (
        today_str, now_str, trade_data["signal_type"], trade_data["symbol"],
        trade_data["spot_price"], trade_data["entry_price"], trade_data["sl_price"],
        trade_data["target_price"], trade_data["quantity"]
    ))
    
    trade_id = cursor.lastrowid
    
    # Increment daily count
    cursor.execute("""
        INSERT INTO daily_tracker (trade_date, trade_count, total_pnl_rupees, is_locked)
        VALUES (?, 1, 0.0, 0)
        ON CONFLICT(trade_date) DO UPDATE SET trade_count = trade_count + 1
    """, (today_str,))
    
    conn.commit()
    conn.close()
    
    log_event(f"TRADE_ENTERED_{trade_data['signal_type']}", f"Trade #{trade_id} on {trade_data['symbol']} @ {trade_data['entry_price']}")
    return trade_id

def record_trade_exit(trade_id: int, exit_price: float, exit_reason: str, pnl_points: float, pnl_rupees: float):
    conn = get_connection()
    cursor = conn.cursor()
    
    now_str = config.get_now_str_ist()
    today_str = config.get_today_ist()
    
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
    
    conn.commit()
    conn.close()
    
    log_event("TRADE_EXITED", f"Trade #{trade_id} closed @ {exit_price} | PnL: {pnl_points:.1f} pts (₹{pnl_rupees:.2f}) | Reason: {exit_reason}")

def lock_system_today(reason: str):
    conn = get_connection()
    cursor = conn.cursor()
    today_str = config.get_today_ist()
    cursor.execute("""
        UPDATE daily_tracker SET is_locked = 1, lock_reason = ? WHERE trade_date = ?
    """, (reason, today_str))
    conn.commit()
    conn.close()
    log_event("SYSTEM_LOCKED", f"System locked for {today_str}: {reason}")

def get_active_trade() -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM trades WHERE status = 'ACTIVE' ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_all_trades(limit: int = 20) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

# Initialize tables when module is imported
init_db()
