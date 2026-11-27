"""
Risk Manager & 13 Iron Armor Rules Engine
Validates every incoming webhook before any order can be processed.
"""

from datetime import datetime, time
from typing import Tuple, Dict, Any, Optional
import logging
import config
import database

logger = logging.getLogger("IronBot.RiskManager")

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
    max_trades = getattr(config, "get_max_daily_trades", lambda: config.MAX_DAILY_TRADES)()
    if stats.get("trade_count", 0) >= max_trades:
        database.lock_system_today(f"Max daily trades limit ({max_trades}) reached")
        return False, f"Max daily trade limit ({max_trades}) reached. Zero overtrading!"
        
    # 3. Daily Loss Limit (Rule 2)
    max_loss = getattr(config, "get_max_daily_loss", lambda: config.MAX_DAILY_LOSS_RUPEES)()
    if stats.get("total_pnl_rupees", 0.0) <= -max_loss:
        database.lock_system_today(f"Daily loss limit (-₹{max_loss}) hit")
        return False, f"Daily max loss (-₹{max_loss}) hit. Capital protection active!"

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

        # 6B. Volatility-Gated Midday Defense Window (Rule 5B: 11:30 to 13:15 IST)
        # Protects against low-liquidity theta decay chop without starving trade count.
        midday_start = getattr(config, "MIDDAY_START_TIME", time(11, 30))
        midday_end = getattr(config, "MIDDAY_END_TIME", time(13, 15))
        min_midday_range = getattr(config, "MIDDAY_MIN_CANDLE_RANGE", 18.0)
        
        if midday_start <= current_time <= midday_end:
            c_range = float(signal_data.get("candle_range") or 0.0)
            if c_range == 0.0 and signal_data.get("candle_high") is not None and signal_data.get("candle_low") is not None:
                c_range = abs(float(signal_data["candle_high"]) - float(signal_data["candle_low"]))
            
            if c_range < min_midday_range:
                return False, f"MIDDAY_CHOP_DEFENSE: 5m candle range ({c_range:.1f} pts) < {min_midday_range:.1f} pts threshold. Skipping midday low-liquidity trap (11:30-13:15)!"
            else:
                logger.info(f"⚡ [MIDDAY MOMENTUM APPROVED] Active candle range ({c_range:.1f} pts >= {min_midday_range:.1f} pts). Allowing rapid midday momentum scalp!")

    # 7. Debounce / Duplicate Webhook Check (Rule 6)
    signal_type = signal_data.get("signal", "").upper()
    if not is_test and database.is_duplicate_alert(signal_type, config.DEBOUNCE_WINDOW_SECONDS):
        return False, f"Duplicate signal detected within {config.DEBOUNCE_WINDOW_SECONDS}s window. Debounce active!"

    # 8. Exhaustion Giant Candle Filter (Rule 10 — Upgraded for Momentum vs Exhaustion)
    candle_range = float(signal_data.get("candle_range") or 0.0)
    max_range = getattr(config, "MAX_CANDLE_RANGE_POINTS", 65.0)
    
    if candle_range > max_range:
        # Absolute ceiling: Black swan mega bar (> 85 pts / ~4x ATR) is always skipped
        if candle_range > 85.0:
            return False, f"Mega-bar volatility spike ({candle_range:.1f} pts > 85 pts max cap). Risk too high!"

        # Check if candle is an institutional Marubozu Breakout or true Wick Exhaustion
        if signal_data.get("candle_high") is not None and signal_data.get("candle_close") is not None:
            c_high = float(signal_data["candle_high"])
            c_close = float(signal_data["candle_close"])
            c_low_val = signal_data.get("candle_low")
            c_low = float(c_low_val) if c_low_val is not None else (c_high - candle_range)
            is_ce = "CE" in signal_type or ("BUY" in signal_type and "PE" not in signal_type)
            
            if is_ce and candle_range > 0:
                upper_wick = max(0.0, c_high - c_close)
                wick_ratio = upper_wick / candle_range
                if wick_ratio > 0.35:
                    return False, f"Exhaustion candle: Heavy upper wick rejection ({upper_wick:.1f} pts, {wick_ratio*100:.0f}% of range). Do not buy top!"
                else:
                    logger.info(f"⚡ [MOMENTUM BREAKOUT] Large candle ({candle_range:.1f} pts) accepted: Closed in top {(1-wick_ratio)*100:.0f}% of bar (Solid Bullish Body).")
            elif not is_ce and candle_range > 0:
                lower_wick = max(0.0, c_close - c_low)
                wick_ratio = lower_wick / candle_range
                if wick_ratio > 0.35:
                    return False, f"Exhaustion candle: Heavy lower wick bounce ({lower_wick:.1f} pts, {wick_ratio*100:.0f}% of range). Do not buy bottom!"
                else:
                    logger.info(f"⚡ [MOMENTUM BREAKDOWN] Large candle ({candle_range:.1f} pts) accepted: Closed in bottom {(1-wick_ratio)*100:.0f}% of bar (Solid Bearish Body).")
        else:
            # High/Close not provided: safely treat oversized candle as exhaustion
            return False, f"Exhaustion candle detected ({candle_range:.1f} pts > {max_range} pts max). Do not buy top!"

    # 9. Capital & Wallet Margin Protection (Rule 15: ₹50K Wallet Cap)
    wallet = database.get_wallet_stats()
    if wallet.get("is_preservation_triggered", False):
        database.lock_system_today(f"Capital preservation floor reached (₹{wallet['current_balance']:,.2f} <= ₹{wallet['preservation_floor']:,.2f})")
        return False, f"Capital Preservation Circuit Breaker: Wallet balance (₹{wallet['current_balance']:,.2f}) hit floor (₹{wallet['preservation_floor']:,.2f}). Trading locked to protect capital!"

    est_premium = float(signal_data.get("entry_price") or 220.0)
    qty = int(signal_data.get("quantity") or config.DEFAULT_LOT_SIZE)
    required_margin = round(est_premium * qty, 2)
    if required_margin > wallet["available_margin"]:
        return False, f"Insufficient Wallet Capital: Required margin ₹{required_margin:,.2f} > Available margin ₹{wallet['available_margin']:,.2f} in ₹50K Account!"

    max_margin_allowed = wallet["current_balance"] * getattr(config, "MAX_MARGIN_PER_TRADE_PCT", 0.50)
    if required_margin > max_margin_allowed:
        return False, f"Position Sizing Cap: Required margin ₹{required_margin:,.2f} exceeds max 50% wallet allocation (₹{max_margin_allowed:,.2f})!"

    # 10. Higher-Timeframe Trend Shield (Rule 16: Never fade extreme trend days)
    # If Nifty has gained >= 0.30% or +60 pts from Open, block counter-trend PE scalp trades.
    # Conversely, if Nifty is down <= -0.30% or -60 pts from Open, block counter-trend CE scalp trades.
    if not is_test:
        is_pe = "PE" in signal_type or "PUT" in signal_type or "SHORT" in signal_type
        is_ce = "CE" in signal_type or ("BUY" in signal_type and not is_pe)
        
        # Method A: Direct Day Open validation from intraday 5m chart
        day_open = float(signal_data.get("day_open") or 0.0)
        spot_price = float(signal_data.get("spot") or 0.0)
        htf_pts_limit = getattr(config, "HTF_TREND_SHIELD_POINTS", 60.0)
        
        if day_open > 0 and spot_price > 0:
            intraday_diff = round(spot_price - day_open, 1)
            if intraday_diff <= -htf_pts_limit and is_ce:
                return False, f"HTF Trend Shield: Nifty is down {intraday_diff} pts from Open ({day_open:.1f}). Counter-trend CE buy strictly blocked in heavy downtrend!"
            if intraday_diff >= htf_pts_limit and is_pe:
                return False, f"HTF Trend Shield: Nifty is up +{intraday_diff} pts from Open ({day_open:.1f}). Counter-trend PE buy strictly blocked in heavy uptrend!"

        # Method B: Global Macro / Exchange change %
        try:
            import macro_radar
            macro = macro_radar.get_macro_sentiment()
            nifty_chg = float(macro.get("nifty", {}).get("change", 0.0) or 0.0)
            thresh_pct = getattr(config, "HTF_TREND_SHIELD_PCT", 0.30)
            
            if nifty_chg >= thresh_pct and is_pe:
                return False, f"Extreme Trend Shield: Nifty is up +{nifty_chg:.2f}% (Raging Bull Day). Counter-trend PE entries strictly blocked!"
            if nifty_chg <= -thresh_pct and is_ce:
                return False, f"Extreme Trend Shield: Nifty is down {nifty_chg:.2f}% (Bloodbath Day). Counter-trend CE entries strictly blocked!"
        except Exception as e:
            logger.debug(f"Trend shield check skipped: {e}")

    return True, "APPROVED"

def calculate_dynamic_greek_brackets(entry_premium: float, delta: float = 0.50, spot_atr: float = 22.0) -> Tuple[float, float, str]:
    """
    Buchanan + Passarelli Dynamic Greeks Stop-Loss & Target Engine:
    1. Spot Breathing Room: ΔS_stop = SPOT_NOISE_MULTIPLIER * ATR * sqrt(VIX / 15.0)
    2. Delta Translation: SL_pts = |Delta| * ΔS_stop + half_spread
    3. Target Engine: TARGET_RR_RATIO (1:1.8) Risk-to-Reward Ratio (with Gamma acceleration)
    """
    if not getattr(config, "ENABLE_GREEKS_DYNAMIC_SL", True):
        return get_vix_adaptive_brackets()
        
    try:
        import macro_radar
        macro = macro_radar.get_macro_sentiment()
        vix_obj = (macro or {}).get("vix") or {}
        vix = float(vix_obj.get("price") or 15.0)
    except Exception:
        vix = 15.0

    # Sanitize and clamp inputs against domain errors (negative or zero VIX/ATR/premium)
    vix = max(8.0, min(35.0, vix))
    entry_premium = max(10.0, float(entry_premium or 100.0))
    spot_atr = max(5.0, float(spot_atr or 22.0))

    # 1. Spot Invalidation Distance (1.3x ATR scaled by VIX volatility)
    multiplier = getattr(config, "SPOT_NOISE_MULTIPLIER", 1.3)
    vix_scale = max(0.8, min(1.5, (vix / 15.0) ** 0.5))
    spot_noise_distance = max(18.0, spot_atr * multiplier * vix_scale)
    
    # 2. Option SL Distance via Buchanan Taylor Expansion (Slide 50)
    abs_delta = max(0.20, min(0.95, abs(delta)))
    
    # Theoretical option drop = |Delta| * ΔS_stop + Half-Spread Buffer (0.75 pts)
    sl_points_raw = (abs_delta * spot_noise_distance) + (config.BID_ASK_FRICTION_POINTS / 2.0)
    
    # Guardrails: Min floor (6.0 pts) and Max Cap (15% of premium and MAX_SL_POINTS)
    min_sl = getattr(config, "MIN_SL_POINTS", 6.0)
    max_sl_pct = getattr(config, "MAX_SL_PREMIUM_PCT", 0.15)
    max_allowed_sl = max(min_sl + 2.0, entry_premium * max_sl_pct)
    max_sl_pts_cap = getattr(config, "get_max_sl_points", lambda: getattr(config, "MAX_SL_POINTS", 13.0))()
    if max_sl_pts_cap is not None:
        max_allowed_sl = min(max_allowed_sl, float(max_sl_pts_cap))
    
    sl_points = max(min_sl, min(sl_points_raw, max_allowed_sl))
    sl_points = round(sl_points, 1)
    
    # 3. Institutional Risk-Reward Target (Passarelli 1:1.8 RRR)
    rr_ratio = getattr(config, "TARGET_RR_RATIO", 1.8)
    target_points = round(sl_points * rr_ratio, 1)
    
    regime = (
        f"GREEK_DYNAMIC (Δ={abs_delta:.2f}, SpotRoom={spot_noise_distance:.1f}pts, VIX={vix:.1f}) "
        f"-> SL: -{sl_points}pts | TP: +{target_points}pts (1:{rr_ratio:.1f} RR)"
    )
    return sl_points, target_points, regime

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
    
    # Dynamic Greek-Engineered SL & Target (Buchanan + Passarelli Model)
    spot_atr = float(signal_data.get("atr", getattr(config, "DEFAULT_SPOT_ATR", 22.0)))
    sl_points, target_points, vix_regime = calculate_dynamic_greek_brackets(
        entry_premium=ask_fill,
        delta=delta,
        spot_atr=spot_atr
    )
    
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
            
            thresh = getattr(config, "RUNNER_MACRO_THRESHOLD", 15)
            is_pullback = signal_data.get("pullback_reentry", False)
            day_open = float(signal_data.get("day_open") or 0.0)
            intraday_pts = (spot - day_open) if day_open > 0 else 0.0

            # Technical & Derivatives Confluence (Local Market Reality)
            bullish_confluence = (macro_score >= thresh and pcr >= 1.0) or \
                                 (pcr >= 1.10 and spot >= put_wall) or \
                                 (is_pullback and intraday_pts >= 20.0)

            bearish_confluence = (macro_score <= -thresh and pcr <= 1.0) or \
                                 (pcr <= 0.88 and spot <= call_wall) or \
                                 (is_pullback and intraday_pts <= -20.0)

            if is_call and bullish_confluence and macro_score >= -35:
                is_runner = True
                runner_reason = f"🟢 BULLISH CONFLUENCE (PCR: {pcr:.2f}, SpotVsOpen: {intraday_pts:+.1f}pts) -> Trend Runner Mode Activated!"
            elif not is_call and bearish_confluence and macro_score <= 35:
                is_runner = True
                runner_reason = f"🔴 BEARISH CONFLUENCE (PCR: {pcr:.2f}, SpotVsOpen: {intraday_pts:+.1f}pts) -> Trend Runner Mode Activated!"
        except Exception as e:
            logger.warning(f"Error evaluating confluence runner: {e}")
            is_runner = False
            runner_reason = ""
            
    # Micro-SL and Target Engineering
    # On normal days, clamp target to realistic Hit & Run (+14.0 pts) to lock high win rate (60%+)
    # On confluence runner days, preserve full dynamic Greek target (+26+ pts) for ratchet trailing
    if not is_runner:
        target_points = getattr(config, "DEFAULT_TARGET_POINTS", 14.0)

    # If within midday window, use rapid scalp target (+11.0 pts)
    now_t = config.get_now_ist().time()
    midday_start = getattr(config, "MIDDAY_START_TIME", time(11, 30))
    midday_end = getattr(config, "MIDDAY_END_TIME", time(13, 15))
    if midday_start <= now_t <= midday_end:
        target_points = getattr(config, "MIDDAY_SCALP_TARGET_POINTS", 11.0)

    sl_price = round(ask_fill - sl_points, 2)
    target_price = round(ask_fill + target_points, 2)
    
    # Sanity Guardrail: SL must ALWAYS be below entry for BUY positions
    if sl_price >= ask_fill:
        logger.warning(f"⚠️ Inverted SL detected! sl={sl_price} >= entry={ask_fill}. sl_points={sl_points}. Forcing fallback SL.")
        sl_price = round(ask_fill - config.DEFAULT_MICRO_SL_POINTS, 2)
        target_price = round(ask_fill + config.DEFAULT_TARGET_POINTS, 2)
        
    if target_price <= ask_fill:
        logger.warning(f"⚠️ Inverted Target detected! target={target_price} <= entry={ask_fill}. Forcing fallback Target.")
        target_price = round(ask_fill + config.DEFAULT_TARGET_POINTS, 2)
    
    return {
        "signal_type": "BUY_CE" if is_call else "BUY_PE",
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
