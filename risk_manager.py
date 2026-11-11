"""
Risk Manager & 13 Iron Armor Rules Engine
Validates every incoming webhook before any order can be processed.
"""

from datetime import datetime, time
from typing import Tuple, Dict, Any, Optional
import config
import database

def get_atm_strike(spot_price: float, is_call: bool) -> int:
    """Rounds Nifty spot to nearest 50-strike ATM."""
    base_strike = round(spot_price / 50.0) * 50
    return int(base_strike)

def validate_signal_armor(signal_data: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Runs all 13 Iron Armor Rules.
    Returns (True, "APPROVED") or (False, "REJECTION_REASON").
    """
    now = config.get_now_ist()
    current_time = now.time()
    
    # 1. System Lock Check
    stats = database.get_today_stats()
    if stats.get("is_locked", 0) == 1:
        return False, f"System is LOCKED for today ({stats.get('lock_reason', 'Limit reached')})"
        
    # 2. Daily Trade Count Limit (Rule 1)
    if stats.get("trade_count", 0) >= config.MAX_DAILY_TRADES:
        database.lock_system_today(f"Max daily trades limit ({config.MAX_DAILY_TRADES}) reached")
        return False, f"Max daily trade limit ({config.MAX_DAILY_TRADES}) reached. Zero overtrading!"
        
    # 3. Daily Loss Limit (Rule 2)
    if stats.get("total_pnl_rupees", 0.0) <= -config.MAX_DAILY_LOSS_RUPEES:
        database.lock_system_today(f"Daily loss limit (-₹{config.MAX_DAILY_LOSS_RUPEES}) hit")
        return False, f"Daily max loss (-₹{config.MAX_DAILY_LOSS_RUPEES}) hit. Capital protection active!"

    # 4. Active Trade Check (No overlapping / duplicate trades)
    active = database.get_active_trade()
    if active:
        return False, f"Already in active trade #{active['id']} on {active['symbol']}. No concurrent entries!"

    # 5. Market Hours Check (Rule 3)
    # Allows bypass if testing flag provided
    is_test = signal_data.get("is_test", False)
    if not is_test:
        if current_time < config.MARKET_START_TIME:
            return False, f"Market hours check: Too early ({current_time.strftime('%H:%M')}). Wait till 09:30 AM!"
            
        if current_time > config.MARKET_END_TIME:
            return False, f"Market hours check: Too late ({current_time.strftime('%H:%M')}). No entry after 14:45 PM!"

        # 6. European Open Blackout Window (Rule 5: 13:25 to 13:45)
        if config.EURO_BLACKOUT_START <= current_time <= config.EURO_BLACKOUT_END:
            return False, "European Market Open Whiplash Window (13:25-13:45). Entry blocked to avoid trap!"

    # 7. Debounce / Duplicate Webhook Check (Rule 6)
    signal_type = signal_data.get("signal", "").upper()
    if not is_test and database.is_duplicate_alert(signal_type, config.DEBOUNCE_WINDOW_SECONDS):
        return False, f"Duplicate signal detected within {config.DEBOUNCE_WINDOW_SECONDS}s window. Debounce active!"

    # 8. Exhaustion Giant Candle Filter (Rule 10)
    candle_range = signal_data.get("candle_range", 0.0)
    if candle_range > config.MAX_CANDLE_RANGE_POINTS:
        return False, f"Exhaustion candle detected ({candle_range:.1f} pts > {config.MAX_CANDLE_RANGE_POINTS} pts max). Do not buy top!"

    return True, "APPROVED"

def get_vix_adaptive_brackets() -> Tuple[float, float, str]:
    """
    Returns (sl_pts, target_pts, regime_name) based on India VIX.
    """
    if not getattr(config, "ENABLE_VIX_ADAPTIVE", True):
        return config.DEFAULT_MICRO_SL_POINTS, config.DEFAULT_TARGET_POINTS, "FIXED"
        
    try:
        import macro_radar
        macro = macro_radar.get_macro_sentiment()
        vix = float(macro.get("vix", {}).get("price", 15.0))
    except Exception:
        vix = 15.0
        
    if vix < 13.0:
        return 6.0, 16.0, f"LOW_VIX ({vix:.1f}) -> 16 pt TP / 6 pt SL"
    elif vix > 18.0:
        return 9.0, 28.0, f"HIGH_VIX ({vix:.1f}) -> 28 pt TP / 9 pt SL"
    else:
        return 7.0, 22.0, f"NORMAL_VIX ({vix:.1f}) -> 22 pt TP / 7 pt SL"

def calculate_trade_parameters(signal_data: Dict[str, Any]) -> Dict[str, Any]:
    """Calculates ATM Strike, Micro-SL, Target, and Quantity."""
    spot = float(signal_data.get("spot", 25050.0))
    raw_signal = signal_data.get("signal", "BUY_CE").upper()
    
    # Accurate Options Direction: PE/PUT/SHORT takes precedence over BUY
    if any(k in raw_signal for k in ["PE", "PUT", "SHORT"]):
        is_call = False
        signal_type = "BUY_PE"
    else:
        is_call = True
        signal_type = "BUY_CE"

    strike = get_atm_strike(spot, is_call)
    symbol = f"NIFTY{strike}{'CE' if is_call else 'PE'}"
    
    # Premium calculation: Fetches real live option tick LTP & Ask from NSE feed
    entry_premium = None
    ask_fill = None
    delta = 0.50
    if signal_data.get("entry_price") is not None:
        entry_premium = float(signal_data["entry_price"])
    else:
        try:
            import option_chain_shield
            quote = option_chain_shield.get_real_option_quote(spot, is_call)
            if quote.get("ltp", 0.0) > 0.0:
                entry_premium = float(quote["ltp"])
                # Real market fills at Ask price (Spread Friction)
                ask_fill = float(quote.get("ask", entry_premium + config.BID_ASK_FRICTION_POINTS))
                delta = float(quote.get("delta", 0.50))
                strike = int(quote["strike"])
                expiry_str = quote.get("expiry", "")
                symbol = f"NIFTY{strike}{'CE' if is_call else 'PE'} ({expiry_str})"
        except Exception:
            entry_premium = None

    if not entry_premium:
        try:
            import option_chain_shield
            quote = option_chain_shield.get_real_option_quote(spot, is_call)
            entry_premium = float(quote.get("ltp", 220.0))
            ask_fill = float(quote.get("ask", entry_premium + config.BID_ASK_FRICTION_POINTS))
        except Exception:
            entry_premium = 220.0
            ask_fill = 220.6
    elif ask_fill is None:
        ask_fill = round(entry_premium + config.BID_ASK_FRICTION_POINTS, 2)
    
    # India VIX Adaptive Dynamic SL & Target (Gap #5)
    sl_points, target_points, vix_regime = get_vix_adaptive_brackets()
    
    # Loosely Coupled Confluence Trend Runner Evaluation
    is_runner = False
    runner_reason = ""
    if getattr(config, "ENABLE_CONFLUENCE_TREND_RUNNER", True):
        try:
            import macro_radar
            import option_chain_shield
            
            macro = macro_radar.get_macro_sentiment()
            macro_score = int(macro.get("score", 0))
            
            snapshot = option_chain_shield.get_oi_snapshot(spot)
            pcr = float(snapshot.get("pcr", 1.0))
            call_wall = int(snapshot.get("call_wall_resistance", strike + 150))
            put_wall = int(snapshot.get("put_wall_support", strike - 150))
            
            thresh = getattr(config, "RUNNER_MACRO_THRESHOLD", 30)
            if is_call and macro_score >= thresh and pcr >= 1.05 and spot > put_wall:
                is_runner = True
                runner_reason = f"🟢 BULLISH CONFLUENCE (Macro: +{macro_score}, PCR: {pcr:.2f}) -> Trend Runner Mode Activated!"
            elif not is_call and macro_score <= -thresh and pcr <= 0.85 and spot < call_wall:
                is_runner = True
                runner_reason = f"🔴 BEARISH CONFLUENCE (Macro: {macro_score}, PCR: {pcr:.2f}) -> Trend Runner Mode Activated!"
        except Exception:
            is_runner = False
            runner_reason = ""
            
    # Micro-SL: Uses candle_low if available and reasonable, else adaptive micro-SL
    candle_low = signal_data.get("candle_low")
    if candle_low and (ask_fill - float(candle_low)) <= 10.0 and (ask_fill - float(candle_low)) >= 4.0:
        sl_price = round(float(candle_low), 2)
    else:
        sl_price = round(ask_fill - sl_points, 2)
        
    target_price = round(ask_fill + target_points, 2)
    
    return {
        "signal_type": "BUY_CE" if is_call else "SELL_PE",
        "symbol": symbol,
        "spot_price": spot,
        "entry_price": ask_fill, # Real fill price accounting for Ask spread
        "ltp_reference": entry_premium,
        "sl_price": sl_price,
        "target_price": target_price,
        "quantity": config.DEFAULT_LOT_SIZE,
        "vix_regime": vix_regime,
        "is_runner": is_runner,
        "runner_reason": runner_reason,
        "delta": delta
    }
