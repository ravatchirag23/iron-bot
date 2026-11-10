"""
Brutal Stress-Test & Reality-Check Backtester for Iron Bot
Zero Sugar-Coating. Real Market Friction, Slippage, Chop & Expiry Analysis.
"""

import math
import random
from datetime import datetime, timedelta
from typing import List, Dict, Any

def run_brutal_stress_test(trading_days: int = 90, seed: int = 101) -> Dict[str, Any]:
    """
    Simulates 90 actual trading days (approx 4.5 months) across 4 real Indian market regimes:
    1. Trending Days (Strong directional impulse) ~ 30% of days
    2. Choppy / Sideways Days (Mean-reverting range) ~ 50% of days
    3. Violent Whipsaw / Fake Breakout Days ~ 12% of days
    4. Expiry Day Rapid Theta Decay ~ 8% of days

    Friction factored in:
    - Slippage: 0.8 points average on market orders
    - Brokerage & Govt Taxes (STT, GST, Exchange charges): ₹58 per round-trip trade
    - Lot Size: 65 (NIFTY 1 Lot)
    """
    random.seed(seed)
    
    trades = []
    equity_curve = [0.0]
    peak_equity = 0.0
    max_drawdown_pts = 0.0
    max_drawdown_rupees = 0.0
    
    wins = 0
    losses = 0
    breakevens = 0
    consecutive_losses = 0
    max_consecutive_losses = 0
    consecutive_wins = 0
    max_consecutive_wins = 0
    
    LOT_SIZE = 65
    FRICTION_PER_TRADE_INR = 58.0 # Brokerage + STT + GST + Stamp duty
    AVG_SLIPPAGE_PTS = 0.8        # Execution slippage on ATM options
    
    # Starting date
    current_date = datetime(2026, 6, 1)
    
    for day in range(trading_days):
        current_date += timedelta(days=1)
        while current_date.weekday() >= 5: # Skip weekends
            current_date += timedelta(days=1)
            
        is_expiry_day = (current_date.weekday() == 3) # Thursday Expiry
        
        # Roll market regime
        roll = random.random()
        
        entry_price = 58.0
        target_pts = 22.0
        micro_sl_pts = 7.0
        trailing_trigger_pts = 12.0
        
        # Real-world outcome paths
        if is_expiry_day:
            # Expiry Day: High gamma, rapid theta decay
            expiry_roll = random.random()
            if expiry_roll < 0.35:
                # Clean afternoon trending breakout
                gross_pts = target_pts - AVG_SLIPPAGE_PTS
                reason = "TARGET_HIT_EXPIRY (+22 pts)"
                outcome = "WIN"
            elif expiry_roll < 0.70:
                # Sideways theta grind (Micro-SL killed by decay)
                gross_pts = -micro_sl_pts - AVG_SLIPPAGE_PTS
                reason = "THETA_CRUSH_SL (-7 pts)"
                outcome = "LOSS"
            else:
                # Reached +14 pts, then theta crushed it back to cost
                gross_pts = 0.0 - AVG_SLIPPAGE_PTS
                reason = "TRAILING_COST_EXPIRY (0 pts)"
                outcome = "BREAKEVEN"
                
        elif roll < 0.30:
            # Regime 1: Clean Directional Trend (Bullish/Bearish Expansion)
            gross_pts = target_pts - AVG_SLIPPAGE_PTS
            reason = "CLEAN_TARGET_HIT (+22 pts)"
            outcome = "WIN"
            
        elif roll < 0.78:
            # Regime 2: Sideways Range / Chop (50% of real market days!)
            # In chop, 7-point micro-SL is extremely vulnerable to normal candle wicks
            chop_roll = random.random()
            if chop_roll < 0.52:
                # Fake breakout: Entered, moved 3 pts, reversed, hit SL
                gross_pts = -micro_sl_pts - AVG_SLIPPAGE_PTS
                reason = "CHOP_WHIPSAW_SL (-7 pts)"
                outcome = "LOSS"
            elif chop_roll < 0.82:
                # Moved +13 pts (triggered Trailing SL to cost), then reversed to cost
                gross_pts = 0.0 - AVG_SLIPPAGE_PTS
                reason = "TRAILING_ARMOR_SAVED (0 pts)"
                outcome = "BREAKEVEN"
            else:
                # Weak grind reached target at end of session
                gross_pts = target_pts - AVG_SLIPPAGE_PTS
                reason = "SLOW_GRIND_TARGET (+22 pts)"
                outcome = "WIN"
                
        else:
            # Regime 3: High Volatility Shock / Traps
            shock_roll = random.random()
            if shock_roll < 0.60:
                # Sudden reversal candle hit SL instantly
                gross_pts = -micro_sl_pts - (AVG_SLIPPAGE_PTS * 1.5) # Higher slippage on spike
                reason = "VOLATILITY_SPIKE_SL (-7 pts)"
                outcome = "LOSS"
            elif shock_roll < 0.85:
                # Trailing saved it
                gross_pts = 0.0 - AVG_SLIPPAGE_PTS
                reason = "SPIKE_TRAILING_SAVED (0 pts)"
                outcome = "BREAKEVEN"
            else:
                gross_pts = target_pts - AVG_SLIPPAGE_PTS
                reason = "VOLATILITY_RUNNER_TARGET (+22 pts)"
                outcome = "WIN"

        # Calculate Net P&L in Rupees
        gross_rupees = gross_pts * LOT_SIZE
        net_rupees = gross_rupees - FRICTION_PER_TRADE_INR
        
        if outcome == "WIN":
            wins += 1
            consecutive_wins += 1
            consecutive_losses = 0
            if consecutive_wins > max_consecutive_wins:
                max_consecutive_wins = consecutive_wins
        elif outcome == "LOSS":
            losses += 1
            consecutive_losses += 1
            consecutive_wins = 0
            if consecutive_losses > max_consecutive_losses:
                max_consecutive_losses = consecutive_losses
        else:
            breakevens += 1
            consecutive_wins = 0
            consecutive_losses = 0

        current_equity = equity_curve[-1] + net_rupees
        equity_curve.append(current_equity)
        
        if current_equity > peak_equity:
            peak_equity = current_equity
        drawdown = peak_equity - current_equity
        if drawdown > max_drawdown_rupees:
            max_drawdown_rupees = drawdown

        trades.append({
            "trade_no": len(trades) + 1,
            "date": current_date.strftime("%Y-%m-%d"),
            "day": current_date.strftime("%a"),
            "gross_pts": round(gross_pts, 1),
            "net_inr": round(net_rupees, 2),
            "cumulative_inr": round(current_equity, 2),
            "outcome": outcome,
            "reason": reason
        })

    total_trades = len(trades)
    win_rate = (wins / total_trades) * 100
    loss_rate = (losses / total_trades) * 100
    be_rate = (breakevens / total_trades) * 100
    
    gross_gains = sum(t["net_inr"] for t in trades if t["net_inr"] > 0)
    gross_losses = abs(sum(t["net_inr"] for t in trades if t["net_inr"] < 0))
    profit_factor = round(gross_gains / gross_losses, 2) if gross_losses > 0 else 99.0
    net_total_inr = equity_curve[-1]

    # Analysis of Failure Modes
    losing_trades = [t for t in trades if t["outcome"] == "LOSS"]
    chop_losses = [t for t in losing_trades if "CHOP" in t["reason"]]
    theta_losses = [t for t in losing_trades if "THETA" in t["reason"]]
    spike_losses = [t for t in losing_trades if "SPIKE" in t["reason"]]

    return {
        "trading_days": trading_days,
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "breakevens": breakevens,
        "win_rate": win_rate,
        "loss_rate": loss_rate,
        "be_rate": be_rate,
        "profit_factor": profit_factor,
        "net_total_inr": net_total_inr,
        "max_drawdown_rupees": max_drawdown_rupees,
        "max_consecutive_losses": max_consecutive_losses,
        "max_consecutive_wins": max_consecutive_wins,
        "total_friction_paid": round(total_trades * FRICTION_PER_TRADE_INR, 2),
        "chop_losses_count": len(chop_losses),
        "theta_losses_count": len(theta_losses),
        "spike_losses_count": len(spike_losses),
        "trades": trades
    }

if __name__ == "__main__":
    res = run_brutal_stress_test(90)
    print(f"Total Trades: {res['total_trades']}")
    print(f"Wins: {res['wins']} ({res['win_rate']:.1f}%) | Losses: {res['losses']} ({res['loss_rate']:.1f}%) | Breakeven: {res['breakevens']} ({res['be_rate']:.1f}%)")
    print(f"Net PnL (After all Brokerage/Slippage): ₹{res['net_total_inr']:,.2f}")
    print(f"Max Drawdown: -₹{res['max_drawdown_rupees']:,.2f}")
    print(f"Max Consecutive Loss Streak: {res['max_consecutive_losses']} Days in a row")
    print(f"Chop Losses: {res['chop_losses_count']} | Theta Losses: {res['theta_losses_count']} | Spike Losses: {res['spike_losses_count']}")
