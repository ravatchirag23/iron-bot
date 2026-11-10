"""
Pre-Market Macro Sentiment Radar for Iron Bot
Fetches US Markets (Dow/Nasdaq), India VIX, and Nifty Momentum via resilient public feeds.
Produces a normalized Macro Sentiment Score (-100 to +100) and Morning Briefing.
Strictly loosely coupled: Zero crashes, graceful fallback to NEUTRAL on network errors.
"""

import urllib.request
import json
import logging
from typing import Dict, Any, Tuple
import telegram_notifier

logger = logging.getLogger("IronBot.MacroRadar")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def fetch_yahoo_quote(ticker: str) -> Tuple[float, float]:
    """
    Fetches regularMarketPrice and percent change from public chart endpoint.
    Returns (price, pct_change). Falls back to (0.0, 0.0) on failure.
    """
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=2d"
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=4) as response:
            data = json.loads(response.read().decode("utf-8"))
            meta = data["chart"]["result"][0]["meta"]
            price = float(meta.get("regularMarketPrice", 0.0))
            prev = float(meta.get("chartPreviousClose", price))
            pct_change = round(((price - prev) / prev) * 100, 2) if prev else 0.0
            return price, pct_change
    except Exception as e:
        logger.warning(f"Could not fetch quote for {ticker}: {e}")
        return 0.0, 0.0

def get_macro_sentiment() -> Dict[str, Any]:
    """
    Computes aggregated Macro Sentiment Score (-100 Extreme Bearish to +100 Extreme Bullish).
    Evaluates:
    - Dow Jones (^DJI)
    - Nasdaq Composite (^IXIC)
    - India VIX (^INDIAVIX)
    - NIFTY 50 (^NSEI)
    """
    dow_price, dow_chg = fetch_yahoo_quote("%5EDJI")
    nas_price, nas_chg = fetch_yahoo_quote("%5EIXIC")
    vix_price, vix_chg = fetch_yahoo_quote("%5EINDIAVIX")
    nifty_price, nifty_chg = fetch_yahoo_quote("%5ENSEI")

    score = 0
    factors = []

    # 1. US Markets Weight (Max ±40 points)
    us_avg = (dow_chg + nas_chg) / 2.0 if (dow_chg and nas_chg) else (dow_chg or nas_chg or 0.0)
    if us_avg >= 1.0:
        score += 35
        factors.append("Strong US Overnight Rally (+35)")
    elif us_avg >= 0.3:
        score += 20
        factors.append("Moderate US Overnight Gains (+20)")
    elif us_avg <= -1.0:
        score -= 35
        factors.append("Sharp US Overnight Selloff (-35)")
    elif us_avg <= -0.3:
        score -= 20
        factors.append("Moderate US Overnight Losses (-20)")
    else:
        factors.append("US Markets Flat/Neutral (0)")

    # 2. India VIX Regime (Max ±30 points)
    # VIX < 13: Complacent / tight chop range
    # VIX 13 - 17: Healthy trending sweet spot
    # VIX > 18: Fear / wide unpredictable swings
    vix_regime = "NORMAL"
    if vix_price > 0:
        if vix_price < 12.5:
            vix_regime = "CHOP_RISK (Low VIX < 12.5)"
            score -= 10 # Tight chop penalty
            factors.append("Low VIX: High sideways chop risk (-10)")
        elif 12.5 <= vix_price <= 16.5:
            vix_regime = "SWEET_SPOT (Stable 12.5 - 16.5)"
            score += 15 # Healthy liquidity
            factors.append("VIX in Sweet Spot: Clean trend follow (+15)")
        elif vix_price > 18.0:
            vix_regime = "HIGH_VOLATILITY (Fear > 18.0)"
            factors.append("High VIX: Slippage & sudden wick risk")

    # 3. Nifty Index Momentum (Max ±30 points)
    if nifty_chg >= 0.5:
        score += 25
        factors.append("Nifty Opening Strong (+25)")
    elif nifty_chg <= -0.5:
        score -= 25
        factors.append("Nifty Opening Weak (-25)")
    elif nifty_chg > 0:
        score += 10
        factors.append("Nifty Mild Positive (+10)")
    elif nifty_chg < 0:
        score -= 10
        factors.append("Nifty Mild Negative (-10)")

    # Clamping score between -100 and +100
    score = max(-100, min(100, score))

    # Bias classification
    if score >= 40:
        bias = "🟢 BULLISH TAILWIND"
        advisory = "CE (Call) signals carry higher statistical edge. Beware of PE counter-trend traps."
    elif score <= -40:
        bias = "🔴 BEARISH HEADWIND"
        advisory = "PE (Put) signals carry higher statistical edge. Beware of CE counter-trend traps."
    else:
        bias = "⚪ NEUTRAL / BALANCED"
        advisory = "No strong macro edge. Follow pure UT Bot Dual-Trigger with strict 7 pt Micro-SL."

    return {
        "score": score,
        "bias": bias,
        "advisory": advisory,
        "dow": {"price": dow_price, "change": dow_chg},
        "nasdaq": {"price": nas_price, "change": nas_chg},
        "vix": {"price": vix_price, "change": vix_chg, "regime": vix_regime},
        "nifty": {"price": nifty_price, "change": nifty_chg},
        "factors": factors
    }

def send_morning_briefing() -> bool:
    """Sends formatted Pre-Market Cockpit briefing to Telegram."""
    macro = get_macro_sentiment()
    
    dow_sign = "+" if macro["dow"]["change"] >= 0 else ""
    nas_sign = "+" if macro["nasdaq"]["change"] >= 0 else ""
    vix_sign = "+" if macro["vix"]["change"] >= 0 else ""
    nifty_sign = "+" if macro["nifty"]["change"] >= 0 else ""

    msg = (
        f"🧭 *IRON BOT: PRE-MARKET MACRO RADAR*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🇺🇸 *Wall Street (Dow):* {macro['dow']['price']:,.0f} ({dow_sign}{macro['dow']['change']:.2f}%)\n"
        f"💻 *Tech (Nasdaq):* {macro['nasdaq']['price']:,.0f} ({nas_sign}{macro['nasdaq']['change']:.2f}%)\n"
        f"⚡ *India VIX:* {macro['vix']['price']:.2f} ({vix_sign}{macro['vix']['change']:.2f}%) [{macro['vix']['regime']}]\n"
        f"🇮🇳 *Nifty 50 Spot:* {macro['nifty']['price']:,.2f} ({nifty_sign}{macro['nifty']['change']:.2f}%)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 *MACRO BIAS:* {macro['bias']} (Score: `{macro['score']:+d}/100`)\n"
        f"💡 *Desk Advisory:* {macro['advisory']}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔒 *Iron Bot Status:* Armed on Render Cloud (1-Trade Rule Active)"
    )
    return telegram_notifier.send_telegram_alert(msg)

if __name__ == "__main__":
    import pprint
    res = get_macro_sentiment()
    pprint.pprint(res)
    send_morning_briefing()
