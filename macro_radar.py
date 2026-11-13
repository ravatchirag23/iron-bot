"""
Pre-Market Macro Sentiment Radar for Iron Bot (Institutional Edition)
Fetches:
1. GIFT Nifty (formerly SGX / Singapore Nifty) live from NSE IX
2. US Markets (Dow Jones ^DJI, Nasdaq ^IXIC)
3. Brent Crude Oil (BZ=F)
4. USD/INR Exchange Rate (USDINR=X)
5. India VIX (^INDIAVIX)
6. NIFTY 50 Spot (^NSEI)

Produces a normalized Macro Sentiment Score (-100 to +100) and Morning Briefing.
Strictly loosely coupled: Zero crashes, graceful fallback to NEUTRAL on network errors.
"""

import urllib.request
import json
import logging
import ssl
from typing import Dict, Any, Tuple
import telegram_notifier
import config

logger = logging.getLogger("IronBot.MacroRadar")

_cached_macro = None
_last_macro_time = 0.0
MACRO_CACHE_TTL = 30.0 # 30-second cache for high-frequency dashboard polling

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE

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
        with urllib.request.urlopen(req, context=_ssl_ctx, timeout=4) as response:
            data = json.loads(response.read().decode("utf-8"))
            meta = data["chart"]["result"][0]["meta"]
            price = float(meta.get("regularMarketPrice", 0.0))
            prev = float(meta.get("chartPreviousClose", price))
            pct_change = round(((price - prev) / prev) * 100, 2) if prev else 0.0
            return price, pct_change
    except Exception as e:
        logger.warning(f"Could not fetch quote for {ticker}: {e}")
        return 0.0, 0.0

def fetch_gift_nifty() -> Tuple[float, float]:
    """
    Fetches live GIFT Nifty (formerly SGX / Singapore Nifty) price from NSE IX deep-intraday-graph.
    Returns (price, session_pct_change). Falls back to (0.0, 0.0) on failure.
    """
    try:
        req = urllib.request.Request(
            "https://www.nseix.com/api/deep-intraday-graph",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.nseix.com/"
            }
        )
        with urllib.request.urlopen(req, context=_ssl_ctx, timeout=5) as r:
            d = json.loads(r.read().decode("utf-8"))
            for series in reversed(d.get("data", [])):
                pts = series.get("data", [])
                if pts:
                    last_price = float(pts[-1][1])
                    first_price = float(pts[0][1])
                    pct = round(((last_price - first_price) / first_price) * 100, 2) if first_price else 0.0
                    return last_price, pct
    except Exception as e:
        logger.warning(f"Could not fetch GIFT Nifty from NSE IX: {e}")
    return 0.0, 0.0

def get_macro_sentiment(force: bool = False) -> Dict[str, Any]:
    """
    Computes aggregated Institutional Macro Sentiment Score (-100 Extreme Bearish to +100 Extreme Bullish).
    Evaluates:
    - GIFT Nifty (formerly SGX Nifty) from NSE IX
    - US Overnight Wall Street (Dow Jones, Nasdaq)
    - Brent Crude Oil (BZ=F)
    - USD/INR Currency (USDINR=X)
    - India VIX (^INDIAVIX)
    - NIFTY 50 Spot (^NSEI)
    """
    global _cached_macro, _last_macro_time
    import time
    now = time.time()
    if not force and _cached_macro and (now - _last_macro_time) < MACRO_CACHE_TTL:
        return _cached_macro

    gift_price, gift_chg = fetch_gift_nifty()
    dow_price, dow_chg = fetch_yahoo_quote("%5EDJI")
    nas_price, nas_chg = fetch_yahoo_quote("%5EIXIC")
    crude_price, crude_chg = fetch_yahoo_quote("BZ=F")
    usdinr_price, usdinr_chg = fetch_yahoo_quote("USDINR=X")
    vix_price, vix_chg = fetch_yahoo_quote("%5EINDIAVIX")
    nifty_price, nifty_chg = fetch_yahoo_quote("%5ENSEI")

    score = 0
    factors = []

    # 1. GIFT Nifty (Offshore Price Discovery Lead) — Max ±30 pts
    if gift_price > 0:
        if nifty_price > 0:
            gift_gap = round(gift_price - nifty_price, 1)
            if gift_gap >= 50.0:
                score += 30
                factors.append(f"GIFT Nifty Gap-Up Lead (+{gift_gap} pts) (+30)")
            elif gift_gap >= 20.0:
                score += 15
                factors.append(f"GIFT Nifty Mild Gap-Up (+{gift_gap} pts) (+15)")
            elif gift_gap <= -50.0:
                score -= 30
                factors.append(f"GIFT Nifty Gap-Down Lead ({gift_gap} pts) (-30)")
            elif gift_gap <= -20.0:
                score -= 15
                factors.append(f"GIFT Nifty Mild Gap-Down ({gift_gap} pts) (-15)")
            else:
                factors.append(f"GIFT Nifty Flat Gap ({gift_gap:+} pts) (0)")
        else:
            if gift_chg >= 0.4:
                score += 25
                factors.append(f"GIFT Nifty Overnight Surge (+{gift_chg}%) (+25)")
            elif gift_chg <= -0.4:
                score -= 25
                factors.append(f"GIFT Nifty Overnight Slide ({gift_chg}%) (-25)")

    # 2. US Markets Weight — Max ±25 pts
    us_avg = (dow_chg + nas_chg) / 2.0 if (dow_chg and nas_chg) else (dow_chg or nas_chg or 0.0)
    if us_avg >= 1.0:
        score += 25
        factors.append(f"Strong US Overnight Rally ({us_avg:+.2f}%) (+25)")
    elif us_avg >= 0.3:
        score += 15
        factors.append(f"Moderate US Overnight Gains ({us_avg:+.2f}%) (+15)")
    elif us_avg <= -1.0:
        score -= 25
        factors.append(f"Sharp US Overnight Selloff ({us_avg:+.2f}%) (-25)")
    elif us_avg <= -0.3:
        score -= 15
        factors.append(f"Moderate US Overnight Losses ({us_avg:+.2f}%) (-15)")
    else:
        factors.append("US Markets Flat/Neutral (0)")

    # 3. Brent Crude Oil Impact (Net Importer) — Max ±15 pts
    # Rising crude (>+1.5%) hurts India macros; Falling crude (<-1.5%) boosts India
    if crude_price > 0:
        if crude_chg >= 1.5:
            score -= 15
            factors.append(f"Brent Crude Spike (+{crude_chg:.2f}%) [Inflationary Risk] (-15)")
        elif crude_chg <= -1.5:
            score += 15
            factors.append(f"Brent Crude Cooling ({crude_chg:.2f}%) [Margin Tailwind] (+15)")
        else:
            factors.append(f"Brent Crude Stable (${crude_price:.2f}) (0)")

    # 4. USD/INR Currency & FII Flight — Max ±10 pts
    # Rupee depreciation (>+0.3% USDINR) signals FII outflow pressure
    if usdinr_price > 0:
        if usdinr_chg >= 0.3:
            score -= 10
            factors.append(f"Rupee Weakening (+{usdinr_chg:.2f}% USDINR) [FII Flight Risk] (-10)")
        elif usdinr_chg <= -0.3:
            score += 10
            factors.append(f"Rupee Strengthening ({usdinr_chg:.2f}% USDINR) [Inflow Friendly] (+10)")
        else:
            factors.append(f"USD/INR Steady (₹{usdinr_price:.2f}) (0)")

    # 5. India VIX Regime — Max ±20 pts
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

    # Clamping score between -100 and +100
    score = max(-100, min(100, score))

    # Bias classification
    if score >= 35:
        bias = "🟢 BULLISH TAILWIND"
        advisory = "CE (Call) signals carry higher statistical edge. Beware of PE counter-trend traps."
    elif score <= -35:
        bias = "🔴 BEARISH HEADWIND"
        advisory = "PE (Put) signals carry higher statistical edge. Beware of CE counter-trend traps."
    else:
        bias = "⚪ NEUTRAL / BALANCED"
        advisory = "No strong macro edge. Follow pure UT Bot Dual-Trigger with strict 7 pt Micro-SL."

    _cached_macro = {
        "score": score,
        "bias": bias,
        "advisory": advisory,
        "gift_nifty": {"price": gift_price, "change": gift_chg},
        "dow": {"price": dow_price, "change": dow_chg},
        "nasdaq": {"price": nas_price, "change": nas_chg},
        "crude": {"price": crude_price, "change": crude_chg},
        "usdinr": {"price": usdinr_price, "change": usdinr_chg},
        "vix": {"price": vix_price, "change": vix_chg, "regime": vix_regime},
        "nifty": {"price": nifty_price, "change": nifty_chg},
        "factors": factors
    }
    _last_macro_time = now
    return _cached_macro

def send_morning_briefing() -> bool:
    """Sends formatted Pre-Market Cockpit briefing to Telegram."""
    macro = get_macro_sentiment()
    
    gift_sign = "+" if macro["gift_nifty"]["change"] >= 0 else ""
    dow_sign = "+" if macro["dow"]["change"] >= 0 else ""
    nas_sign = "+" if macro["nasdaq"]["change"] >= 0 else ""
    crude_sign = "+" if macro["crude"]["change"] >= 0 else ""
    usdinr_sign = "+" if macro["usdinr"]["change"] >= 0 else ""
    vix_sign = "+" if macro["vix"]["change"] >= 0 else ""
    nifty_sign = "+" if macro["nifty"]["change"] >= 0 else ""

    msg = (
        f"🧭 *IRON BOT: PRE-MARKET MACRO RADAR*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🌏 *GIFT Nifty (NSE IX):* {macro['gift_nifty']['price']:,.1f} ({gift_sign}{macro['gift_nifty']['change']:.2f}%)\n"
        f"🇺🇸 *Wall Street (Dow):* {macro['dow']['price']:,.0f} ({dow_sign}{macro['dow']['change']:.2f}%)\n"
        f"💻 *Tech (Nasdaq):* {macro['nasdaq']['price']:,.0f} ({nas_sign}{macro['nasdaq']['change']:.2f}%)\n"
        f"🛢 *Brent Crude:* ${macro['crude']['price']:.2f} ({crude_sign}{macro['crude']['change']:.2f}%)\n"
        f"💵 *USD/INR:* ₹{macro['usdinr']['price']:.2f} ({usdinr_sign}{macro['usdinr']['change']:.2f}%)\n"
        f"⚡ *India VIX:* {macro['vix']['price']:.2f} ({vix_sign}{macro['vix']['change']:.2f}%) [{macro['vix']['regime']}]\n"
        f"🇮🇳 *Nifty 50 Spot:* {macro['nifty']['price']:,.2f} ({nifty_sign}{macro['nifty']['change']:.2f}%)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 *MACRO BIAS:* {macro['bias']} (Score: `{macro['score']:+d}/100`)\n"
        f"💡 *Desk Advisory:* {macro['advisory']}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔒 *Iron Bot Status:* Armed on AWS EC2 Mumbai ({getattr(config, 'MAX_DAILY_TRADES', 4)}-Trades / Day Quota Active)"
    )
    return telegram_notifier.send_telegram_alert(msg)

if __name__ == "__main__":
    import pprint
    res = get_macro_sentiment()
    pprint.pprint(res)

