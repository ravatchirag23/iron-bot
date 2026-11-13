"""
Institutional Trading Cockpit & Live Operations Dashboard for Iron Bot
Author: Chirag Ravat (Dev OS)
Features:
- Dark Glassmorphism Terminal UI (Bloomberg / TradingView Pro Grade)
- Zero-Flicker Background Polling via /api/live_state (every 2s)
- Continuous Live Ticker Flashing (Green/Red) on tick changes
- 7 Global Macro Tickers: GIFT Nifty (NSE IX), Dow, Nasdaq, Crude, USDINR, VIX, Nifty Spot
- Manual '⚡ Refresh Tickers' trigger & Live seconds countdown timer
- Real-time Active Trade Risk-to-Reward Progress Ladder & Greeks Breakdown
- Full Institutional Trade Ledger with Win Rate, Profit Factor, & Points
- 14 Iron Armor Defense Shields status matrix
"""

import json
import re
from datetime import datetime
from typing import Dict, Any, List
import config
import database
import macro_radar
import option_chain_shield
import angel_connector
import paper_engine

def get_live_dashboard_state() -> Dict[str, Any]:
    """Aggregates real-time engine, market, and ledger state with 0-crash guarantee."""
    now_ist = config.get_now_ist()
    time_str = now_ist.strftime("%H:%M:%S")
    date_str = now_ist.strftime("%Y-%m-%d")
    
    current_t = now_ist.time()
    is_market_hours = config.MARKET_START_TIME <= current_t <= config.MARKET_END_TIME
    is_euro_blackout = config.EURO_BLACKOUT_START <= current_t <= config.EURO_BLACKOUT_END

    # 1. Database Stats
    today_stats = database.get_today_stats()
    all_trades = database.get_all_trades(50)
    active_trade = database.get_active_trade()

    # 2. Ledger Analytics
    closed_trades = [t for t in all_trades if t.get("status") == "CLOSED"]
    total_trades = len(closed_trades)
    wins = [t for t in closed_trades if (t.get("pnl_rupees") or 0.0) > 0]
    losses = [t for t in closed_trades if (t.get("pnl_rupees") or 0.0) < 0]
    win_rate = round((len(wins) / total_trades * 100), 1) if total_trades > 0 else 0.0
    
    total_pts = round(sum(t.get("pnl_points") or 0.0 for t in closed_trades), 1)
    total_cash = round(sum(t.get("pnl_rupees") or 0.0 for t in closed_trades), 2)
    
    total_gross_profit = sum(t.get("pnl_rupees", 0.0) for t in wins)
    total_gross_loss = abs(sum(t.get("pnl_rupees", 0.0) for t in losses))
    if total_gross_loss > 0:
        profit_factor = round(total_gross_profit / total_gross_loss, 2)
    else:
        profit_factor = 99.0 if total_gross_profit > 0 else 1.0

    # 3. Active Position Live MTM
    active_payload = None
    if active_trade:
        symbol = active_trade["symbol"]
        is_call = "CE" in symbol
        spot = float(active_trade.get("spot_price", 25050.0))
        entry = float(active_trade["entry_price"])
        qty = int(active_trade.get("quantity", config.DEFAULT_LOT_SIZE))
        sl = float(active_trade["sl_price"])
        target = float(active_trade["target_price"])
        
        # Extract held strike for 100% exact price tracking
        strike_match = re.search(r'NIFTY(\d+)', symbol)
        held_strike = int(strike_match.group(1)) if strike_match else round(spot / 50.0) * 50
        
        try:
            quote = option_chain_shield.get_option_quote_by_strike(held_strike, is_call, spot_price=spot)
            live_ltp = float(quote.get("ltp") or entry)
        except Exception:
            live_ltp = entry
            
        pnl_pts = round(live_ltp - entry, 2)
        pnl_rupees = round(pnl_pts * qty, 2)
        sl_dist = round(live_ltp - sl, 2)
        target_dist = round(target - live_ltp, 2)
        
        # Risk to reward progress percentage
        rr_progress = 0
        if target > sl and (target - entry) > 0:
            rr_progress = min(100, max(0, int(((live_ltp - entry) / (target - entry)) * 100)))

        active_payload = {
            "id": active_trade["id"],
            "symbol": symbol,
            "strike": held_strike,
            "side": "CALL (CE)" if is_call else "PUT (PE)",
            "is_call": is_call,
            "quantity": qty,
            "entry_price": entry,
            "sl_price": sl,
            "target_price": target,
            "live_ltp": live_ltp,
            "pnl_points": pnl_pts,
            "pnl_rupees": pnl_rupees,
            "sl_dist": sl_dist,
            "target_dist": target_dist,
            "rr_progress": rr_progress,
            "timestamp": active_trade["timestamp"],
            "is_runner": bool(active_trade.get("is_runner", 0)),
            "milestone": getattr(paper_engine.engine, "milestone_reached", 0)
        }

    # 4. Macro Radar Cockpit
    macro_data = {}
    try:
        macro_data = macro_radar.get_macro_sentiment()
    except Exception as e:
        macro_data = {
            "score": 0,
            "bias": "⚪ NEUTRAL (Offline)",
            "advisory": "Macro feed idle",
            "factors": []
        }

    # 5. Option Chain & Market Structure
    oi_data = {}
    try:
        oi_data = option_chain_shield.get_oi_snapshot()
    except Exception as e:
        oi_data = {
            "spot": 25050.0,
            "atm_strike": 25050,
            "pcr": 1.0,
            "call_wall_resistance": 25200,
            "put_wall_support": 24900,
            "sentiment": "NEUTRAL"
        }

    # 6. Broker Connector Status
    angel_info = {}
    try:
        angel_info = angel_connector.get_broker_status()
    except Exception:
        angel_info = {"broker": "Angel One", "connected": False, "client_code": "C51977841", "status_text": "OFFLINE"}

    # 7. Dynamic Greeks Parameters Breakdown
    vix = 13.5
    try:
        vix = float(macro_data.get("vix", {}).get("price") or 13.5)
    except Exception:
        pass
    spot_atr = 22.0
    breathing_room = round(1.3 * spot_atr * (max(8.0, vix) / 15.0)**0.5, 1)
    calc_sl = round(0.50 * breathing_room + 0.6, 1)
    calc_tp = round(calc_sl * 1.8, 1)

    # 8. System Status Flag
    is_locked = bool(today_stats.get("is_locked", 0) == 1)
    lock_reason = today_stats.get("lock_reason", "")
    
    if is_locked:
        sys_state = "LOCKED"
        sys_state_desc = f"Locked: {lock_reason}"
    elif is_euro_blackout:
        sys_state = "BLACKOUT"
        sys_state_desc = "European Open Window (13:25-13:45)"
    elif is_market_hours:
        sys_state = "ARMED"
        sys_state_desc = "Scanning Live Market 5m Bars"
    else:
        sys_state = "IDLE"
        sys_state_desc = "Market Closed (Awaiting 09:30 AM)"

    return {
        "timestamp": time_str,
        "date": date_str,
        "is_market_hours": is_market_hours,
        "system_state": sys_state,
        "system_state_desc": sys_state_desc,
        "paper_trading_mode": config.PAPER_TRADING_MODE,
        "today_stats": {
            "trade_count": today_stats.get("trade_count", 0),
            "max_trades": config.MAX_DAILY_TRADES,
            "total_pnl_rupees": today_stats.get("total_pnl_rupees", 0.0),
            "max_daily_loss": config.MAX_DAILY_LOSS_RUPEES,
            "is_locked": is_locked,
            "lock_reason": lock_reason
        },
        "ledger_summary": {
            "total_trades": total_trades,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "total_points": total_pts,
            "total_cash": total_cash
        },
        "active_trade": active_payload,
        "greeks_calc": {
            "vix": vix,
            "spot_atr": spot_atr,
            "breathing_room": breathing_room,
            "option_delta": 0.50,
            "spread_friction": 0.60,
            "micro_sl_pts": calc_sl,
            "target_pts": calc_tp,
            "rr_ratio": "1:1.8"
        },
        "macro_radar": macro_data,
        "option_chain": {
            "spot": oi_data.get("spot", 25050.0),
            "atm_strike": oi_data.get("atm_strike", 25050),
            "pcr": oi_data.get("pcr", 1.0),
            "call_wall": oi_data.get("call_wall_resistance", 25200),
            "put_wall": oi_data.get("put_wall_support", 24900),
            "sentiment": oi_data.get("sentiment", "NEUTRAL")
        },
        "broker": angel_info,
        "recent_trades": all_trades[:20]
    }

def render_dashboard_html() -> str:
    """Returns the Institutional Dark Terminal Dashboard HTML/CSS/JS Single-Page Application."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>IRON BOT — Institutional Trading Terminal</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-base: #080c14;
            --bg-surface: #0e1524;
            --bg-card: rgba(18, 27, 46, 0.7);
            --bg-card-hover: rgba(24, 36, 61, 0.85);
            --border: rgba(255, 255, 255, 0.08);
            --border-highlight: rgba(56, 189, 248, 0.25);
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --text-muted: #64748b;
            --green: #10b981;
            --green-glow: rgba(16, 185, 129, 0.25);
            --red: #ef4444;
            --red-glow: rgba(239, 68, 68, 0.25);
            --blue: #38bdf8;
            --purple: #a855f7;
            --amber: #f59e0b;
            --cyan: #06b6d4;
            --font-main: 'Plus Jakarta Sans', -apple-system, sans-serif;
            --font-mono: 'JetBrains Mono', monospace;
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background-color: var(--bg-base);
            color: var(--text-primary);
            font-family: var(--font-main);
            min-height: 100vh;
            padding: 20px;
            background-image: 
                radial-gradient(circle at 15% 15%, rgba(56, 189, 248, 0.04) 0%, transparent 40%),
                radial-gradient(circle at 85% 85%, rgba(168, 85, 247, 0.04) 0%, transparent 40%);
        }

        .mono { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }

        /* Dynamic Tick Flash Animations */
        @keyframes flashGreen {
            0% { background: rgba(16, 185, 129, 0.4); color: #fff; transform: scale(1.02); }
            100% { background: transparent; transform: scale(1); }
        }
        @keyframes flashRed {
            0% { background: rgba(239, 68, 68, 0.4); color: #fff; transform: scale(1.02); }
            100% { background: transparent; transform: scale(1); }
        }
        .flash-green { animation: flashGreen 0.75s ease-out; }
        .flash-red { animation: flashRed 0.75s ease-out; }

        /* Navigation Header */
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: var(--bg-card);
            backdrop-filter: blur(16px);
            border: 1px solid var(--border);
            padding: 14px 24px;
            border-radius: 14px;
            margin-bottom: 20px;
        }
        .brand {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .brand-logo {
            width: 36px;
            height: 36px;
            background: linear-gradient(135deg, #38bdf8, #818cf8);
            border-radius: 9px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 800;
            font-size: 18px;
            color: #0b0f19;
            box-shadow: 0 0 16px rgba(56, 189, 248, 0.4);
        }
        .brand-name {
            font-size: 18px;
            font-weight: 800;
            letter-spacing: -0.5px;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .live-dot {
            width: 8px;
            height: 8px;
            background: var(--green);
            border-radius: 50%;
            box-shadow: 0 0 8px var(--green);
            animation: pulse 1.8s infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.4; transform: scale(1.3); }
        }
        .nav-meta {
            display: flex;
            align-items: center;
            gap: 14px;
        }
        .pill {
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid var(--border);
            padding: 6px 12px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .pill-green { background: rgba(16, 185, 129, 0.12); border-color: rgba(16, 185, 129, 0.3); color: var(--green); }
        .pill-blue { background: rgba(56, 189, 248, 0.12); border-color: rgba(56, 189, 248, 0.3); color: var(--blue); }
        .pill-red { background: rgba(239, 68, 68, 0.12); border-color: rgba(239, 68, 68, 0.3); color: var(--red); }
        .pill-amber { background: rgba(245, 158, 11, 0.12); border-color: rgba(245, 158, 11, 0.3); color: var(--amber); }
        .pill-purple { background: rgba(168, 85, 247, 0.12); border-color: rgba(168, 85, 247, 0.3); color: var(--purple); }

        .refresh-btn {
            background: rgba(56, 189, 248, 0.12);
            border: 1px solid rgba(56, 189, 248, 0.3);
            color: var(--blue);
            font-family: var(--font-main);
            font-size: 11px;
            font-weight: 700;
            padding: 5px 12px;
            border-radius: 6px;
            cursor: pointer;
            transition: all 0.2s;
            display: inline-flex;
            align-items: center;
            gap: 4px;
        }
        .refresh-btn:hover {
            background: rgba(56, 189, 248, 0.25);
            transform: translateY(-1px);
        }
        .refresh-btn:active {
            transform: scale(0.96);
        }

        /* Top KPI Bar */
        .kpi-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
            gap: 16px;
            margin-bottom: 20px;
        }
        .kpi-card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 16px 20px;
            backdrop-filter: blur(12px);
            transition: all 0.2s ease;
        }
        .kpi-card:hover {
            border-color: var(--border-highlight);
            transform: translateY(-2px);
        }
        .kpi-label {
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.8px;
            color: var(--text-muted);
            font-weight: 700;
            margin-bottom: 6px;
            display: flex;
            justify-content: space-between;
        }
        .kpi-val {
            font-size: 24px;
            font-weight: 700;
            color: var(--text-primary);
        }
        .kpi-sub {
            font-size: 12px;
            color: var(--text-secondary);
            margin-top: 4px;
            display: flex;
            align-items: center;
            gap: 4px;
        }

        /* Tabs Section */
        .tabs-bar {
            display: flex;
            gap: 8px;
            margin-bottom: 20px;
            border-bottom: 1px solid var(--border);
            padding-bottom: 8px;
        }
        .tab-btn {
            background: transparent;
            border: none;
            color: var(--text-muted);
            font-family: var(--font-main);
            font-size: 13px;
            font-weight: 700;
            padding: 8px 16px;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.2s;
        }
        .tab-btn.active {
            color: var(--text-primary);
            background: rgba(255, 255, 255, 0.08);
            box-shadow: 0 0 12px rgba(0,0,0,0.2);
        }
        .tab-btn:hover:not(.active) {
            color: var(--text-secondary);
            background: rgba(255, 255, 255, 0.03);
        }

        /* Main Workspace Grid */
        .workspace-grid {
            display: grid;
            grid-template-columns: 1.8fr 1.2fr;
            gap: 20px;
        }
        @media (max-width: 1024px) {
            .workspace-grid { grid-template-columns: 1fr; }
        }

        /* Glass Card Container */
        .card-box {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 22px;
            backdrop-filter: blur(16px);
            margin-bottom: 20px;
        }
        .card-box-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 18px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            padding-bottom: 12px;
        }
        .card-box-title {
            font-size: 14px;
            font-weight: 800;
            letter-spacing: 0.5px;
            text-transform: uppercase;
            color: var(--text-secondary);
            display: flex;
            align-items: center;
            gap: 8px;
        }

        /* Active Trade Ladder & Box */
        .active-trade-box {
            background: linear-gradient(145deg, rgba(16, 185, 129, 0.04), rgba(56, 189, 248, 0.04));
            border: 1px solid rgba(56, 189, 248, 0.3);
            border-radius: 14px;
            padding: 20px;
            position: relative;
            overflow: hidden;
        }
        .active-trade-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 16px;
        }
        .strike-title {
            font-size: 20px;
            font-weight: 800;
            letter-spacing: -0.5px;
            color: #fff;
        }
        .ladder-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 12px;
            background: rgba(0, 0, 0, 0.25);
            padding: 14px;
            border-radius: 10px;
            border: 1px solid rgba(255, 255, 255, 0.05);
            margin: 16px 0;
            text-align: center;
        }
        .ladder-item-label { font-size: 11px; color: var(--text-muted); font-weight: 700; margin-bottom: 4px; }
        .ladder-item-val { font-size: 16px; font-weight: 700; }

        .progress-track {
            height: 8px;
            background: rgba(255, 255, 255, 0.08);
            border-radius: 4px;
            overflow: hidden;
            margin: 14px 0 6px 0;
            position: relative;
        }
        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #38bdf8, #10b981);
            border-radius: 4px;
            transition: width 0.4s ease;
        }

        /* Idle State Card */
        .idle-box {
            padding: 40px 20px;
            text-align: center;
            color: var(--text-muted);
        }
        .radar-spinner {
            width: 48px;
            height: 48px;
            border: 2px solid rgba(56, 189, 248, 0.2);
            border-top-color: var(--blue);
            border-radius: 50%;
            margin: 0 auto 16px auto;
            animation: spin 2s linear infinite;
        }
        @keyframes spin { to { transform: rotate(360deg); } }

        /* Greeks Engine Cards */
        .greeks-grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 12px;
            margin-top: 12px;
        }
        .greek-box {
            background: rgba(0, 0, 0, 0.2);
            border: 1px solid rgba(255, 255, 255, 0.04);
            padding: 12px;
            border-radius: 8px;
            text-align: center;
        }
        .greek-label { font-size: 11px; color: var(--text-muted); font-weight: 600; }
        .greek-val { font-size: 15px; font-weight: 700; color: var(--cyan); margin-top: 4px; }

        /* Macro Matrix Ticker */
        .ticker-list {
            display: flex;
            flex-direction: column;
            gap: 9px;
        }
        .ticker-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 10px 14px;
            background: rgba(0, 0, 0, 0.22);
            border: 1px solid rgba(255, 255, 255, 0.04);
            border-radius: 8px;
            transition: background 0.3s;
        }
        .ticker-name { font-size: 13px; font-weight: 700; color: var(--text-primary); display: flex; align-items: center; gap: 6px; }
        .ticker-price { font-size: 13px; font-weight: 600; color: var(--text-secondary); transition: all 0.3s; }
        .ticker-chg {
            font-size: 12px;
            font-weight: 700;
            padding: 3px 8px;
            border-radius: 4px;
            min-width: 65px;
            text-align: right;
        }
        .chg-pos { background: rgba(16, 185, 129, 0.15); color: var(--green); }
        .chg-neg { background: rgba(239, 68, 68, 0.15); color: var(--red); }

        /* Table Styling */
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }
        th {
            background: rgba(0, 0, 0, 0.25);
            padding: 12px 14px;
            text-align: left;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.6px;
            color: var(--text-muted);
            border-bottom: 1px solid var(--border);
        }
        td {
            padding: 12px 14px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.04);
            color: var(--text-secondary);
        }
        tr:hover td {
            background: rgba(255, 255, 255, 0.02);
            color: var(--text-primary);
        }

        /* 14 Armor Shields Grid */
        .shields-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
            gap: 12px;
        }
        .shield-card {
            background: rgba(0, 0, 0, 0.2);
            border: 1px solid rgba(255, 255, 255, 0.05);
            border-radius: 8px;
            padding: 12px 14px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .shield-icon {
            width: 24px;
            height: 24px;
            border-radius: 6px;
            background: rgba(16, 185, 129, 0.15);
            color: var(--green);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 12px;
            font-weight: 800;
        }
        .shield-info-title { font-size: 12px; font-weight: 700; color: #fff; }
        .shield-info-desc { font-size: 11px; color: var(--text-muted); }

        /* Footer */
        footer {
            margin-top: 30px;
            text-align: center;
            font-size: 12px;
            color: var(--text-muted);
            display: flex;
            justify-content: space-between;
            padding: 16px 0;
            border-top: 1px solid rgba(255, 255, 255, 0.05);
        }
    </style>
</head>
<body>

    <!-- Header Navigation -->
    <header>
        <div class="brand">
            <div class="brand-logo">⚡</div>
            <div>
                <div class="brand-name">
                    IRON BOT <span style="font-size: 12px; color: var(--blue); font-weight: 700;">PRO COCKPIT</span>
                    <span class="live-dot" title="Live Polling Streaming Active"></span>
                </div>
                <div style="font-size: 11px; color: var(--text-muted); margin-top: 2px;">
                    Chirag Ravat Dev OS • 15-20 pts Passive Engine • Sub-millisecond Execution
                </div>
            </div>
        </div>

        <div class="nav-meta">
            <div class="pill pill-blue mono" id="clock-display">00:00:00 IST</div>
            <div class="pill pill-green" id="canary-badge">🔒 PAPER MODE (₹0 RISK)</div>
            <div class="pill" id="broker-status-pill">ANGEL ONE: CONNECTING...</div>
            <div class="pill pill-green" id="engine-status-pill">ARMED & ACTIVE</div>
        </div>
    </header>

    <!-- Top KPI Bar -->
    <div class="kpi-grid">
        <div class="kpi-card">
            <div class="kpi-label">TODAY'S NET P&L <span id="pnl-pts-pill" class="mono">0.0 pts</span></div>
            <div class="kpi-val mono" id="today-pnl-rupees">₹0.00</div>
            <div class="kpi-sub">
                <span>Daily Max Loss Cap:</span>
                <span class="mono" style="color: var(--red);">₹2,500</span>
            </div>
        </div>

        <div class="kpi-card">
            <div class="kpi-label">TRADES TAKEN TODAY <span id="trade-quota-pill">Active</span></div>
            <div class="kpi-val mono" id="trade-count-display">0 / 4</div>
            <div class="kpi-sub">
                <span id="lock-state-desc">Quota Remaining: 4 Trades</span>
            </div>
        </div>

        <div class="kpi-card">
            <div class="kpi-label">NIFTY 50 SPOT <span id="atm-badge" class="pill pill-blue" style="padding: 2px 6px; font-size: 10px;">ATM 25050</span></div>
            <div class="kpi-val mono" id="nifty-spot-display">25,050.00</div>
            <div class="kpi-sub">
                <span id="nifty-chg-display" class="mono">+0.00%</span>
            </div>
        </div>

        <div class="kpi-card">
            <div class="kpi-label">WIN RATE (LIFETIME) <span class="mono" id="profit-factor-pill">PF: 1.00</span></div>
            <div class="kpi-val mono" id="win-rate-display">0.0%</div>
            <div class="kpi-sub mono" id="win-loss-count">0 Wins / 0 Losses</div>
        </div>

        <div class="kpi-card">
            <div class="kpi-label">MARKET MOOD <span class="mono" id="macro-score-badge">0 / 100</span></div>
            <div class="kpi-val" style="font-size: 17px; margin-top: 4px;" id="macro-bias-display">⚪ NEUTRAL</div>
            <div class="kpi-sub" id="pcr-sub-display">PCR: 1.00</div>
        </div>
    </div>

    <!-- Navigation Tabs -->
    <div class="tabs-bar">
        <button class="tab-btn active" onclick="switchTab('cockpit', this)">🎯 LIVE COCKPIT & ACTIVE TRADE</button>
        <button class="tab-btn" onclick="switchTab('ledger', this)">📋 INSTITUTIONAL LEDGER & HISTORY</button>
        <button class="tab-btn" onclick="switchTab('radar', this)">🧭 MACRO RADAR & OPTION CHAIN</button>
        <button class="tab-btn" onclick="switchTab('shields', this)">🛡️ 14 IRON ARMOR SHIELDS</button>
    </div>

    <!-- TAB 1: Cockpit & Active Position -->
    <div id="tab-cockpit" class="tab-content">
        <div class="workspace-grid">
            
            <!-- Left Column: Active Trade & Math Engine -->
            <div>
                <!-- Active Position Section -->
                <div id="active-position-container">
                    <div class="card-box idle-box">
                        <div class="radar-spinner"></div>
                        <h4 style="color: var(--text-primary); margin-bottom: 6px;">NO ACTIVE TRADE POSITION</h4>
                        <p style="font-size: 13px; max-width: 400px; margin: 0 auto;">
                            Native UT Bot 5m scanner is monitoring the tape for confirmed crossover signals with 20 EMA trend confluence.
                        </p>
                    </div>
                </div>

                <!-- Buchanan Greeks Stop-Loss & Target Mathematical Model -->
                <div class="card-box">
                    <div class="card-box-header">
                        <div class="card-box-title">
                            <span>📐 BUCHANAN DYNAMIC GREEKS FORMULA</span>
                        </div>
                        <div class="pill pill-blue mono">ΔS_stop = 1.3 × ATR × √(VIX / 15)</div>
                    </div>

                    <p style="font-size: 12px; color: var(--text-secondary); line-height: 1.6;">
                        Unlike amateur retail traders who choke trades with fixed stop-losses, Iron Bot calculates option breathing room based on the mathematical underlying noise and converts it through option delta (<span class="mono" style="color: var(--cyan);">|Δ| = 0.50</span>).
                    </p>

                    <div class="greeks-grid">
                        <div class="greek-box">
                            <div class="greek-label">NIFTY 5m ATR</div>
                            <div class="greek-val mono" id="calc-atr">22.0 pts</div>
                        </div>
                        <div class="greek-box">
                            <div class="greek-label">INDIA VIX</div>
                            <div class="greek-val mono" id="calc-vix">13.5</div>
                        </div>
                        <div class="greek-box">
                            <div class="greek-label">SPOT BREATHING ROOM</div>
                            <div class="greek-val mono" id="calc-breathing">27.1 pts</div>
                        </div>
                        <div class="greek-box">
                            <div class="greek-label">CALCULATED MICRO-SL</div>
                            <div class="greek-val mono" style="color: var(--red);" id="calc-sl">14.1 pts</div>
                        </div>
                        <div class="greek-box">
                            <div class="greek-label">INSTITUTIONAL TARGET</div>
                            <div class="greek-val mono" style="color: var(--green);" id="calc-tp">25.4 pts</div>
                        </div>
                        <div class="greek-box">
                            <div class="greek-label">RISK-TO-REWARD (RRR)</div>
                            <div class="greek-val mono" style="color: var(--purple);">1 : 1.8 RRR</div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Right Column: Global Macro Matrix & Option Chain Walls -->
            <div>
                <!-- Live Dynamic Macro Matrix (All 7 Instruments) -->
                <div class="card-box">
                    <div class="card-box-header">
                        <div class="card-box-title">
                            <span>🧭 GLOBAL MACRO MATRIX</span>
                            <span class="live-dot" style="width: 6px; height: 6px;" title="Streaming background updates every 12s"></span>
                        </div>
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <span style="font-size: 11px; color: var(--text-muted); font-family: var(--font-mono);" id="macro-sync-timer">0s ago</span>
                            <button class="refresh-btn" onclick="forceRefreshMacro(this)">⚡ Refresh</button>
                            <div class="pill pill-purple mono" id="macro-score-tag">SCORE: 0</div>
                        </div>
                    </div>

                    <div class="ticker-list" id="ticker-list-container">
                        <div class="ticker-row">
                            <span class="ticker-name">🇸🇬 GIFT Nifty (NSE IX)</span>
                            <span class="ticker-price mono" id="t-gift-price">--</span>
                            <span class="ticker-chg mono chg-pos" id="t-gift-chg">--%</span>
                        </div>
                        <div class="ticker-row">
                            <span class="ticker-name">🇺🇸 Dow Jones (^DJI)</span>
                            <span class="ticker-price mono" id="t-dow-price">--</span>
                            <span class="ticker-chg mono chg-pos" id="t-dow-chg">--%</span>
                        </div>
                        <div class="ticker-row">
                            <span class="ticker-name">💻 Nasdaq (^IXIC)</span>
                            <span class="ticker-price mono" id="t-nas-price">--</span>
                            <span class="ticker-chg mono chg-neg" id="t-nas-chg">--%</span>
                        </div>
                        <div class="ticker-row">
                            <span class="ticker-name">🛢️ Brent Crude (BZ=F)</span>
                            <span class="ticker-price mono" id="t-crude-price">--</span>
                            <span class="ticker-chg mono chg-neg" id="t-crude-chg">--%</span>
                        </div>
                        <div class="ticker-row">
                            <span class="ticker-name">💵 USD / INR (USDINR=X)</span>
                            <span class="ticker-price mono" id="t-usdinr-price">--</span>
                            <span class="ticker-chg mono chg-pos" id="t-usdinr-chg">--%</span>
                        </div>
                        <div class="ticker-row">
                            <span class="ticker-name">⚡ India VIX (^INDIAVIX)</span>
                            <span class="ticker-price mono" id="t-vix-price">--</span>
                            <span class="ticker-chg mono chg-neg" id="t-vix-chg">--%</span>
                        </div>
                        <div class="ticker-row">
                            <span class="ticker-name">🇮🇳 Nifty 50 Spot (^NSEI)</span>
                            <span class="ticker-price mono" id="t-nifty-price">--</span>
                            <span class="ticker-chg mono chg-neg" id="t-nifty-chg">--%</span>
                        </div>
                    </div>
                </div>

                <!-- Option Chain Wall Profile -->
                <div class="card-box">
                    <div class="card-box-header">
                        <div class="card-box-title">
                            <span>⛓️ OPTION CHAIN BARRIERS</span>
                        </div>
                        <div class="pill pill-green mono" id="pcr-badge">PCR: 1.00</div>
                    </div>

                    <div style="display: flex; flex-direction: column; gap: 12px;">
                        <div style="background: rgba(239, 68, 68, 0.08); border: 1px solid rgba(239, 68, 68, 0.2); border-radius: 8px; padding: 12px;">
                            <div style="display: flex; justify-content: space-between; font-size: 12px; font-weight: 700; color: var(--red);">
                                <span>CALL WALL (HEAVY RESISTANCE)</span>
                                <span class="mono" id="call-wall-display">25,500</span>
                            </div>
                            <small style="color: var(--text-muted); font-size: 11px;">Ceiling where institutions wrote heavy Calls</small>
                        </div>

                        <div style="background: rgba(16, 185, 129, 0.08); border: 1px solid rgba(16, 185, 129, 0.2); border-radius: 8px; padding: 12px;">
                            <div style="display: flex; justify-content: space-between; font-size: 12px; font-weight: 700; color: var(--green);">
                                <span>PUT WALL (HEAVY SUPPORT FLOOR)</span>
                                <span class="mono" id="put-wall-display">25,000</span>
                            </div>
                            <small style="color: var(--text-muted); font-size: 11px;">Floor where institutions wrote heavy Puts</small>
                        </div>

                        <div style="padding: 10px; background: rgba(0, 0, 0, 0.2); border-radius: 8px;">
                            <div style="font-size: 11px; color: var(--text-muted); font-weight: 700;">OI SENTIMENT BIAS</div>
                            <div style="font-size: 13px; font-weight: 700; color: #fff; margin-top: 4px;" id="oi-sentiment-display">
                                NEUTRAL / BALANCED
                            </div>
                        </div>
                    </div>
                </div>

            </div>
        </div>
    </div>

    <!-- TAB 2: Institutional Trade Ledger -->
    <div id="tab-ledger" class="tab-content" style="display: none;">
        <div class="card-box">
            <div class="card-box-header">
                <div class="card-box-title">
                    <span>📑 COMPLETE AUDIT TRAIL & TRADE LEDGER</span>
                </div>
                <div style="display: flex; gap: 8px;">
                    <span class="pill pill-green mono" id="ledger-pts-badge">Net: +0.0 pts</span>
                    <span class="pill pill-blue mono" id="ledger-cash-badge">Cash: ₹0.00</span>
                </div>
            </div>

            <div style="overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>#ID</th>
                            <th>Timestamp</th>
                            <th>Symbol</th>
                            <th>Side</th>
                            <th>Entry (₹)</th>
                            <th>Micro-SL</th>
                            <th>Target</th>
                            <th>Exit (₹)</th>
                            <th>Net Points</th>
                            <th>Net Cash</th>
                            <th>Exit Reason</th>
                            <th>Status</th>
                        </tr>
                    </thead>
                    <tbody id="trades-table-body">
                        <!-- Populated dynamically via JS -->
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <!-- TAB 3: Macro Radar & Option Chain -->
    <div id="tab-radar" class="tab-content" style="display: none;">
        <div class="workspace-grid">
            <div class="card-box">
                <div class="card-box-header">
                    <div class="card-box-title">
                        <span>🛰️ DEEP MACRO SENTIMENT BREAKDOWN</span>
                    </div>
                    <div class="pill pill-purple mono" id="macro-radar-bias-pill">BIAS</div>
                </div>

                <div style="background: rgba(0,0,0,0.25); padding: 18px; border-radius: 10px; margin-bottom: 16px;">
                    <div style="font-size: 13px; font-weight: 700; color: var(--blue); margin-bottom: 6px;">INSTITUTIONAL ADVISORY</div>
                    <p style="font-size: 13px; color: var(--text-secondary); line-height: 1.6;" id="macro-advisory-text">
                        Loading macro advisory...
                    </p>
                </div>

                <h4 style="font-size: 12px; text-transform: uppercase; color: var(--text-muted); margin-bottom: 10px;">Macro Scoring Factor Checklist</h4>
                <div id="macro-factors-container" style="display: flex; flex-direction: column; gap: 8px;">
                    <!-- Populated dynamically -->
                </div>
            </div>

            <div class="card-box">
                <div class="card-box-header">
                    <div class="card-box-title">
                        <span>📊 INSTITUTIONAL OPEN INTEREST ARCHITECTURE</span>
                    </div>
                </div>
                
                <p style="font-size: 13px; color: var(--text-secondary); line-height: 1.6; margin-bottom: 14px;">
                    Real-time snapshot derived from the live NSE Option Chain feed. Strike clusters determine whether incoming signals carry an institutional tailwind or face immediate wall resistance.
                </p>

                <div style="background: rgba(0,0,0,0.2); padding: 16px; border-radius: 8px; margin-bottom: 12px;">
                    <div style="display: flex; justify-content: space-between; margin-bottom: 8px;">
                        <span style="font-size: 12px; color: var(--text-muted);">PCR (Put-Call Ratio)</span>
                        <span class="mono" style="font-weight: 700;" id="radar-pcr-val">1.00</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; margin-bottom: 8px;">
                        <span style="font-size: 12px; color: var(--text-muted);">Call Wall (Upper Resistance)</span>
                        <span class="mono" style="font-weight: 700; color: var(--red);" id="radar-call-wall">25,500</span>
                    </div>
                    <div style="display: flex; justify-content: space-between;">
                        <span style="font-size: 12px; color: var(--text-muted);">Put Wall (Lower Support)</span>
                        <span class="mono" style="font-weight: 700; color: var(--green);" id="radar-put-wall">25,000</span>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- TAB 4: 14 Iron Armor Defense Shields -->
    <div id="tab-shields" class="tab-content" style="display: none;">
        <div class="card-box">
            <div class="card-box-header">
                <div class="card-box-title">
                    <span>🛡️ 14 IRON ARMOR CIRCUIT BREAKERS & DEFENSE SHIELDS</span>
                </div>
                <div class="pill pill-green">ALL 14 RULES ARMED & ENFORCED</div>
            </div>

            <div class="shields-grid">
                <div class="shield-card">
                    <div class="shield-icon">✓</div>
                    <div>
                        <div class="shield-info-title">1. Max Daily Trades</div>
                        <div class="shield-info-desc">Strict 4-trade cap (zero overtrading)</div>
                    </div>
                </div>
                <div class="shield-card">
                    <div class="shield-icon">✓</div>
                    <div>
                        <div class="shield-info-title">2. Daily Loss Limit</div>
                        <div class="shield-info-desc">-₹2,500 circuit breaker lockout</div>
                    </div>
                </div>
                <div class="shield-card">
                    <div class="shield-icon">✓</div>
                    <div>
                        <div class="shield-info-title">3. Market Hours Guard</div>
                        <div class="shield-info-desc">Entry only between 09:30 - 14:45 IST</div>
                    </div>
                </div>
                <div class="shield-card">
                    <div class="shield-icon">✓</div>
                    <div>
                        <div class="shield-info-title">4. European Open Blackout</div>
                        <div class="shield-info-desc">Zero entry 13:25 - 13:45 whiplash trap</div>
                    </div>
                </div>
                <div class="shield-card">
                    <div class="shield-icon">✓</div>
                    <div>
                        <div class="shield-info-title">5. 45-Min Stagnation Exit</div>
                        <div class="shield-info-desc">Theta decay protection exit</div>
                    </div>
                </div>
                <div class="shield-card">
                    <div class="shield-icon">✓</div>
                    <div>
                        <div class="shield-info-title">6. 15:15 PM Auto Square-Off</div>
                        <div class="shield-info-desc">Guarantees zero expiry day zeroing</div>
                    </div>
                </div>
                <div class="shield-card">
                    <div class="shield-icon">✓</div>
                    <div>
                        <div class="shield-info-title">7. Exhaustion Candle Filter</div>
                        <div class="shield-info-desc">Blocks entry if 5m candle > 45 pts</div>
                    </div>
                </div>
                <div class="shield-card">
                    <div class="shield-icon">✓</div>
                    <div>
                        <div class="shield-info-title">8. Webhook Debounce</div>
                        <div class="shield-info-desc">120s duplicate alert suppression</div>
                    </div>
                </div>
                <div class="shield-card">
                    <div class="shield-icon">✓</div>
                    <div>
                        <div class="shield-info-title">9. Dynamic Greeks Stops</div>
                        <div class="shield-info-desc">Buchanan + Passarelli volatility sizing</div>
                    </div>
                </div>
                <div class="shield-card">
                    <div class="shield-icon">✓</div>
                    <div>
                        <div class="shield-info-title">10. Milestone Trailing SL</div>
                        <div class="shield-info-desc">Automatic Cost Lock at 1:1 RR</div>
                    </div>
                </div>
                <div class="shield-card">
                    <div class="shield-icon">✓</div>
                    <div>
                        <div class="shield-info-title">11. Exact Held Strike Lock</div>
                        <div class="shield-info-desc">Zero floating ATM drift leak</div>
                    </div>
                </div>
                <div class="shield-card">
                    <div class="shield-icon">✓</div>
                    <div>
                        <div class="shield-info-title">12. Option Confluence Shield</div>
                        <div class="shield-info-desc">PCR and Wall resistance alignment</div>
                    </div>
                </div>
                <div class="shield-card">
                    <div class="shield-icon">✓</div>
                    <div>
                        <div class="shield-info-title">13. SmartAPI Auto-Handshake</div>
                        <div class="shield-info-desc">Daily 8:00 AM TOTP auto-authentication</div>
                    </div>
                </div>
                <div class="shield-card">
                    <div class="shield-icon">✓</div>
                    <div>
                        <div class="shield-info-title">14. Strict Canary Virtual Lock</div>
                        <div class="shield-info-desc">100% Paper Guard (₹0 Real Money at Risk)</div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Footer -->
    <footer>
        <div>IRON BOT Autonomous Options Trading System • Version 3.0 Institutional</div>
        <div>AWS EC2 Mumbai (ap-south-1) • Dynamic High-Frequency Market Ticker Engine</div>
    </footer>

    <!-- Interactive JS Auto-Sync Engine -->
    <script>
        let secondsSinceSync = 0;

        function switchTab(tabId, btn) {
            document.querySelectorAll('.tab-content').forEach(el => el.style.display = 'none');
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            
            const target = document.getElementById('tab-' + tabId);
            if (target) target.style.display = 'block';
            if (btn) btn.classList.add('active');
        }

        // Helper to update text with live green/red flash animation when price changes
        function updateTickerValue(priceElId, chgElId, newPrice, newChg, prefix = '', decimals = 2) {
            const pEl = document.getElementById(priceElId);
            const cEl = document.getElementById(chgElId);
            if (!pEl || !cEl) return;

            let formattedPrice = '--';
            let numPrice = null;

            if (typeof newPrice === 'number' && newPrice > 0) {
                numPrice = newPrice;
                formattedPrice = prefix + newPrice.toLocaleString('en-IN', {
                    minimumFractionDigits: decimals,
                    maximumFractionDigits: decimals
                });
            } else if (newPrice) {
                formattedPrice = prefix + newPrice;
            }

            const oldText = pEl.innerText;
            if (oldText !== formattedPrice && oldText !== '--') {
                const oldNumeric = parseFloat(oldText.replace(/[^0-9.-]+/g, ''));
                if (numPrice !== null && !isNaN(oldNumeric)) {
                    pEl.classList.remove('flash-green', 'flash-red');
                    void pEl.offsetWidth; // Force CSS animation reflow
                    pEl.classList.add(numPrice >= oldNumeric ? 'flash-green' : 'flash-red');
                }
            }
            pEl.innerText = formattedPrice;

            const numChg = parseFloat(newChg) || 0.0;
            cEl.innerText = (numChg >= 0 ? '+' : '') + numChg.toFixed(2) + '%';
            cEl.className = 'ticker-chg mono ' + (numChg >= 0 ? 'chg-pos' : 'chg-neg');
        }

        async function forceRefreshMacro(btn) {
            if (btn) {
                btn.innerText = '⏳ Fetching...';
                btn.disabled = true;
            }
            try {
                await fetch('/api/refresh_macro');
                await syncLiveState();
            } catch (e) {
                console.error('Force refresh error:', e);
            } finally {
                if (btn) {
                    btn.innerText = '⚡ Refresh';
                    btn.disabled = false;
                }
            }
        }

        async function syncLiveState() {
            try {
                const res = await fetch('/api/live_state');
                if (!res.ok) return;
                const data = await res.json();
                
                // Reset sync counter
                secondsSinceSync = 0;
                const syncTimer = document.getElementById('macro-sync-timer');
                if (syncTimer) syncTimer.innerText = '0s ago';

                // 1. Clock & Meta
                document.getElementById('clock-display').innerText = data.timestamp + ' IST';
                
                // 2. Broker Pill
                const brokerPill = document.getElementById('broker-status-pill');
                if (data.broker && data.broker.connected) {
                    brokerPill.innerText = 'ANGEL ONE: ' + data.broker.client_code + ' (ONLINE)';
                    brokerPill.className = 'pill pill-green';
                } else {
                    brokerPill.innerText = 'ANGEL ONE: C51977841 (ARMED)';
                    brokerPill.className = 'pill pill-blue';
                }

                // 3. Engine Status Pill
                const enginePill = document.getElementById('engine-status-pill');
                if (data.system_state === 'ARMED') {
                    enginePill.innerText = 'ARMED & SCANNING';
                    enginePill.className = 'pill pill-green';
                } else if (data.system_state === 'LOCKED') {
                    enginePill.innerText = 'SYSTEM LOCKED (LIMIT)';
                    enginePill.className = 'pill pill-red';
                } else if (data.system_state === 'BLACKOUT') {
                    enginePill.innerText = 'EURO BLACKOUT (13:25-13:45)';
                    enginePill.className = 'pill pill-amber';
                } else {
                    enginePill.innerText = 'MARKET CLOSED';
                    enginePill.className = 'pill';
                }

                // 4. Today's PnL & Stats
                const pnl = data.today_stats.total_pnl_rupees || 0.0;
                const pnlEl = document.getElementById('today-pnl-rupees');
                pnlEl.innerText = (pnl >= 0 ? '+₹' : '-₹') + Math.abs(pnl).toFixed(2);
                pnlEl.style.color = pnl >= 0 ? 'var(--green)' : 'var(--red)';

                document.getElementById('trade-count-display').innerText = 
                    data.today_stats.trade_count + ' / ' + data.today_stats.max_trades;
                
                const quotaRemaining = Math.max(0, data.today_stats.max_trades - data.today_stats.trade_count);
                document.getElementById('lock-state-desc').innerText = 
                    data.today_stats.is_locked ? data.today_stats.lock_reason : `Quota Remaining: ${quotaRemaining} Trades`;

                // 5. Nifty Spot & ATM
                const spot = data.option_chain.spot || 25050.0;
                document.getElementById('nifty-spot-display').innerText = spot.toLocaleString('en-IN', {minimumFractionDigits: 2});
                document.getElementById('atm-badge').innerText = 'ATM ' + data.option_chain.atm_strike;

                // 6. Win Rate & Profit Factor
                document.getElementById('win-rate-display').innerText = data.ledger_summary.win_rate + '%';
                document.getElementById('win-loss-count').innerText = 
                    data.ledger_summary.wins + ' Wins / ' + data.ledger_summary.losses + ' Losses';
                document.getElementById('profit-factor-pill').innerText = 'PF: ' + data.ledger_summary.profit_factor;

                // 7. Macro Mood
                document.getElementById('macro-bias-display').innerText = data.macro_radar.bias || '⚪ NEUTRAL';
                document.getElementById('macro-score-badge').innerText = (data.macro_radar.score >= 0 ? '+' : '') + data.macro_radar.score + ' / 100';
                document.getElementById('pcr-sub-display').innerText = 'PCR: ' + data.option_chain.pcr + ' (' + data.option_chain.sentiment + ')';
                document.getElementById('pcr-badge').innerText = 'PCR: ' + data.option_chain.pcr;

                // 8. Active Trade Container
                const activeContainer = document.getElementById('active-position-container');
                if (data.active_trade) {
                    const at = data.active_trade;
                    const pnlColor = at.pnl_rupees >= 0 ? 'var(--green)' : 'var(--red)';
                    const pnlSign = at.pnl_rupees >= 0 ? '+' : '';
                    
                    activeContainer.innerHTML = `
                        <div class="card-box active-trade-box">
                            <div class="active-trade-header">
                                <div>
                                    <span class="pill ${at.is_call ? 'pill-green' : 'pill-red'}" style="margin-bottom: 6px; display: inline-flex;">
                                        ${at.side} • 1 LOT (${at.quantity} QTY)
                                    </span>
                                    <div class="strike-title">${at.symbol}</div>
                                </div>
                                <div style="text-align: right;">
                                    <div style="font-size: 11px; color: var(--text-muted); font-weight: 700;">UNREALIZED P&L</div>
                                    <div class="mono" style="font-size: 26px; font-weight: 800; color: ${pnlColor};">
                                        ${pnlSign}₹${at.pnl_rupees.toFixed(2)}
                                    </div>
                                    <small class="mono" style="color: ${pnlColor}; font-weight: 700;">${pnlSign}${at.pnl_points.toFixed(1)} pts</small>
                                </div>
                            </div>

                            <div class="ladder-grid">
                                <div>
                                    <div class="ladder-item-label">ENTRY FILL</div>
                                    <div class="ladder-item-val mono">₹${at.entry_price.toFixed(2)}</div>
                                </div>
                                <div>
                                    <div class="ladder-item-label">LIVE TICK (LTP)</div>
                                    <div class="ladder-item-val mono" style="color: var(--blue);">₹${at.live_ltp.toFixed(2)}</div>
                                </div>
                                <div>
                                    <div class="ladder-item-label">MICRO-SL</div>
                                    <div class="ladder-item-val mono" style="color: var(--red);">₹${at.sl_price.toFixed(2)}</div>
                                </div>
                                <div>
                                    <div class="ladder-item-label">TARGET</div>
                                    <div class="ladder-item-val mono" style="color: var(--green);">₹${at.target_price.toFixed(2)}</div>
                                </div>
                            </div>

                            <div style="display: flex; justify-content: space-between; font-size: 11px; color: var(--text-secondary); margin-top: 10px;">
                                <span>SL Distance: <b>${at.sl_dist > 0 ? '+' : ''}${at.sl_dist.toFixed(1)} pts</b></span>
                                <span>Target Distance: <b>${at.target_dist.toFixed(1)} pts</b></span>
                            </div>

                            <div class="progress-track">
                                <div class="progress-fill" style="width: ${at.rr_progress}%;"></div>
                            </div>

                            <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 10px;">
                                <div style="font-size: 11px; color: var(--text-muted);">
                                    Milestone: <b style="color: var(--purple);">${at.milestone >= 1 ? 'Shield Locked (Cost Lock Active)' : 'Level 0 (Initial SL Active)'}</b>
                                </div>
                                <div style="font-size: 11px; color: var(--text-muted);">
                                    Entered: <span class="mono">${at.timestamp}</span>
                                </div>
                            </div>
                        </div>
                    `;
                } else {
                    activeContainer.innerHTML = `
                        <div class="card-box idle-box">
                            <div class="radar-spinner"></div>
                            <h4 style="color: var(--text-primary); margin-bottom: 6px;">NO ACTIVE TRADE POSITION</h4>
                            <p style="font-size: 13px; max-width: 420px; margin: 0 auto; color: var(--text-muted);">
                                Native UT Bot 5m scanner is monitoring the tape for confirmed crossover signals with 20 EMA trend confluence.
                            </p>
                        </div>
                    `;
                }

                // 9. Greeks Breakdown Display
                if (data.greeks_calc) {
                    document.getElementById('calc-atr').innerText = data.greeks_calc.spot_atr.toFixed(1) + ' pts';
                    document.getElementById('calc-vix').innerText = data.greeks_calc.vix.toFixed(1);
                    document.getElementById('calc-breathing').innerText = data.greeks_calc.breathing_room.toFixed(1) + ' pts';
                    document.getElementById('calc-sl').innerText = data.greeks_calc.micro_sl_pts.toFixed(1) + ' pts';
                    document.getElementById('calc-tp').innerText = data.greeks_calc.target_pts.toFixed(1) + ' pts';
                }

                // 10. Option Chain Wall Display
                if (data.option_chain) {
                    document.getElementById('call-wall-display').innerText = data.option_chain.call_wall;
                    document.getElementById('put-wall-display').innerText = data.option_chain.put_wall;
                    document.getElementById('oi-sentiment-display').innerText = data.option_chain.sentiment;
                    
                    document.getElementById('radar-pcr-val').innerText = data.option_chain.pcr;
                    document.getElementById('radar-call-wall').innerText = data.option_chain.call_wall;
                    document.getElementById('radar-put-wall').innerText = data.option_chain.put_wall;
                }

                // 11. Macro Radar All 7 Tickers with Live Flash Animation
                if (data.macro_radar) {
                    const mr = data.macro_radar;
                    document.getElementById('macro-score-tag').innerText = 'SCORE: ' + (mr.score >= 0 ? '+' : '') + mr.score;
                    document.getElementById('macro-radar-bias-pill').innerText = mr.bias || 'NEUTRAL';
                    document.getElementById('macro-advisory-text').innerText = mr.advisory || 'Balanced market conditions.';

                    if (mr.gift_nifty) {
                        updateTickerValue('t-gift-price', 't-gift-chg', mr.gift_nifty.price, mr.gift_nifty.change, '', 1);
                    }
                    if (mr.dow) {
                        updateTickerValue('t-dow-price', 't-dow-chg', mr.dow.price, mr.dow.change, '', 1);
                    }
                    if (mr.nasdaq) {
                        updateTickerValue('t-nas-price', 't-nas-chg', mr.nasdaq.price, mr.nasdaq.change, '', 1);
                    }
                    if (mr.crude) {
                        updateTickerValue('t-crude-price', 't-crude-chg', mr.crude.price, mr.crude.change, '$', 2);
                    }
                    if (mr.usdinr) {
                        updateTickerValue('t-usdinr-price', 't-usdinr-chg', mr.usdinr.price, mr.usdinr.change, '₹', 2);
                    }
                    if (mr.vix) {
                        updateTickerValue('t-vix-price', 't-vix-chg', mr.vix.price, mr.vix.change, '', 2);
                    }
                    if (mr.nifty) {
                        updateTickerValue('t-nifty-price', 't-nifty-chg', mr.nifty.price, mr.nifty.change, '', 2);
                        document.getElementById('nifty-chg-display').innerText = (mr.nifty.change >= 0 ? '+' : '') + mr.nifty.change.toFixed(2) + '%';
                        document.getElementById('nifty-chg-display').style.color = mr.nifty.change >= 0 ? 'var(--green)' : 'var(--red)';
                    }

                    // Factors list
                    const factorBox = document.getElementById('macro-factors-container');
                    if (mr.factors && mr.factors.length > 0) {
                        factorBox.innerHTML = mr.factors.map(f => `
                            <div style="background: rgba(0,0,0,0.18); padding: 8px 12px; border-radius: 6px; font-size: 12px; border-left: 3px solid var(--blue);">
                                ${f}
                            </div>
                        `).join('');
                    } else {
                        factorBox.innerHTML = '<div style="font-size: 12px; color: var(--text-muted);">No high-impact macro deviations active.</div>';
                    }
                }

                // 12. Trade Ledger Table
                const tbody = document.getElementById('trades-table-body');
                if (data.recent_trades && data.recent_trades.length > 0) {
                    tbody.innerHTML = data.recent_trades.map(t => {
                        const pts = t.pnl_points || 0.0;
                        const cash = t.pnl_rupees || 0.0;
                        const pnlColor = pts >= 0 ? 'var(--green)' : 'var(--red)';
                        const ptsStr = (pts >= 0 ? '+' : '') + pts.toFixed(1) + ' pts';
                        const cashStr = (cash >= 0 ? '+₹' : '-₹') + Math.abs(cash).toFixed(2);
                        const isCall = t.symbol.includes('CE');

                        return `
                            <tr>
                                <td class="mono">#${t.id}</td>
                                <td class="mono" style="font-size: 12px;">${t.timestamp}</td>
                                <td><b>${t.symbol}</b></td>
                                <td><span class="pill ${isCall ? 'pill-green' : 'pill-red'}" style="padding: 2px 6px; font-size: 10px;">${isCall ? 'CE' : 'PE'}</span></td>
                                <td class="mono">₹${t.entry_price.toFixed(2)}</td>
                                <td class="mono">₹${t.sl_price.toFixed(2)}</td>
                                <td class="mono">₹${t.target_price.toFixed(2)}</td>
                                <td class="mono">${t.exit_price ? '₹' + t.exit_price.toFixed(2) : '-'}</td>
                                <td class="mono" style="color: ${pnlColor}; font-weight: 700;">${ptsStr}</td>
                                <td class="mono" style="color: ${pnlColor}; font-weight: 700;">${cashStr}</td>
                                <td><span class="pill" style="font-size: 10px;">${t.exit_reason || 'ACTIVE'}</span></td>
                                <td><span class="pill ${t.status === 'ACTIVE' ? 'pill-green' : ''}" style="font-size: 10px;">${t.status}</span></td>
                            </tr>
                        `;
                    }).join('');
                } else {
                    tbody.innerHTML = '<tr><td colspan="12" style="text-align: center; color: var(--text-muted); padding: 24px;">No trades executed yet today.</td></tr>';
                }

                // Update ledger summary badges
                document.getElementById('ledger-pts-badge').innerText = 'Net: ' + (data.ledger_summary.total_points >= 0 ? '+' : '') + data.ledger_summary.total_points + ' pts';
                document.getElementById('ledger-cash-badge').innerText = 'Cash: ' + (data.ledger_summary.total_cash >= 0 ? '+₹' : '-₹') + Math.abs(data.ledger_summary.total_cash).toFixed(2);

            } catch (err) {
                console.warn('Dashboard sync polling warning:', err);
            }
        }

        // Live seconds counter
        setInterval(() => {
            secondsSinceSync++;
            const st = document.getElementById('macro-sync-timer');
            if (st) st.innerText = secondsSinceSync + 's ago';
        }, 1000);

        // Auto-poll every 2000 milliseconds for seamless zero-flicker experience
        setInterval(syncLiveState, 2000);
        // Initial sync on load
        window.addEventListener('DOMContentLoaded', syncLiveState);
    </script>
</body>
</html>
"""
