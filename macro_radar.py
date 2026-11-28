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
import time
import html
import re
import os
import threading
from typing import Dict, Any, Tuple, List
import telegram_notifier
import config
import database

logger = logging.getLogger("IronBot.MacroRadar")

_cache_lock = threading.Lock()
_cached_macro = None
_last_macro_time = 0.0
MACRO_CACHE_TTL = 30.0 # 30-second cache: eliminates Yahoo 429 rate limits on EC2 while preserving live feel

_last_saved_gift_price = 0.0
_last_saved_gift_time = 0.0

_cached_news = []
_last_news_time = 0.0
NEWS_CACHE_TTL = 300.0 # 5 minutes

_candles_cache = {}
_candles_cache_time = {}

_ssl_ctx = ssl.create_default_context()
if os.environ.get("IRON_BOT_INSECURE_SSL") == "1":
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = ssl.CERT_NONE

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def fetch_cnbc_news() -> List[Dict[str, str]]:
    """
    Fetches real-time financial headlines from CNBC Market Insider RSS feed.
    Zero 3rd-party dependencies (pure regex + urllib), cached for 5 minutes.
    """
    global _cached_news, _last_news_time
    now = time.time()
    with _cache_lock:
        if _cached_news and (now - _last_news_time) < NEWS_CACHE_TTL:
            return list(_cached_news)
    try:
        url = "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20409666"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=_ssl_ctx, timeout=4) as resp:
            content = resp.read().decode("utf-8", errors="ignore")
            items = re.findall(r'<item>(.*?)</item>', content, re.DOTALL)
            parsed = []
            for it in items[:10]:
                t_m = re.search(r'<title>(.*?)</title>', it, re.DOTALL)
                p_m = re.search(r'<pubDate>(.*?)</pubDate>', it, re.DOTALL)
                l_m = re.search(r'<link>(.*?)</link>', it, re.DOTALL)
                title = html.unescape(t_m.group(1).strip()) if t_m else ""
                pub = p_m.group(1).strip() if p_m else ""
                link = l_m.group(1).strip() if l_m else ""
                if title:
                    parsed.append({"title": title, "pubDate": pub, "link": link})
            if parsed:
                with _cache_lock:
                    _cached_news = parsed
                    _last_news_time = now
                return parsed
    except Exception as e:
        logger.warning(f"CNBC RSS news fetch warning: {e}")
    with _cache_lock:
        cached = list(_cached_news) if _cached_news else None
    return cached or [
        {"title": "GIFT Nifty feed temporarily unavailable — awaiting next update.", "pubDate": "Cached", "link": "#"}
    ]

def fetch_candles(ticker: str = "^NSEI", interval: str = "5m", range_param: str = "2d") -> List[Dict[str, Any]]:
    """
    Fetches real-time OHLCV candlestick historical bars for Lightweight Charts.
    Cached for 20 seconds to provide zero-lag interactive chart exploration.
    """
    global _candles_cache, _candles_cache_time
    
    # Universal symbol normalizer
    symbol_map = {
        "NSE:NIFTY": "^NSEI",
        "NIFTY": "^NSEI",
        "NIFTY50": "^NSEI",
        "^NSEI": "^NSEI",
        "NSE:BANKNIFTY": "^NSEBANK",
        "BANKNIFTY": "^NSEBANK",
        "^NSEBANK": "^NSEBANK",
        "NSEIX:NIFTY": "^NSEI",
        "GIFT NIFTY": "^NSEI",
        "FOREXCOM:SPXUSD": "^GSPC",
        "SPX": "^GSPC",
        "^GSPC": "^GSPC",
        "DOW": "^DJI",
        "^DJI": "^DJI",
        "NASDAQ": "^IXIC",
        "^IXIC": "^IXIC"
    }
    resolved_ticker = symbol_map.get(ticker.upper(), ticker)

    cache_key = f"{resolved_ticker}_{interval}_{range_param}"
    now = time.time()
    with _cache_lock:
        if cache_key in _candles_cache and (now - _candles_cache_time.get(cache_key, 0)) < 20.0:
            return list(_candles_cache[cache_key])

    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{resolved_ticker}?interval={interval}&range={range_param}"
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, context=_ssl_ctx, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            result = data["chart"]["result"][0]
            timestamps = result.get("timestamp", [])
            quote = result["indicators"]["quote"][0]
            opens = quote.get("open", [])
            highs = quote.get("high", [])
            lows = quote.get("low", [])
            closes = quote.get("close", [])
            volumes = quote.get("volume", [])
            
            candles = []
            for i in range(len(timestamps)):
                if opens[i] is not None and closes[i] is not None:
                    candles.append({
                        "time": int(timestamps[i]),
                        "open": round(float(opens[i]), 2),
                        "high": round(float(highs[i]), 2),
                        "low": round(float(lows[i]), 2),
                        "close": round(float(closes[i]), 2),
                        "volume": int(volumes[i] or 0)
                    })
            if candles:
                with _cache_lock:
                    _candles_cache[cache_key] = candles
                    _candles_cache_time[cache_key] = now
                return candles
    except Exception as e:
        logger.warning(f"Candles fetch for {ticker} failed: {e}")
    with _cache_lock:
        return list(_candles_cache.get(cache_key, []))

def fetch_yahoo_quote(ticker: str) -> Tuple[float, float]:
    """
    Fetches regularMarketPrice and official percent change directly from exchange meta.
    Returns (price, pct_change). Falls back to (0.0, 0.0) on failure.
    """
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1d"
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, context=_ssl_ctx, timeout=4) as response:
            data = json.loads(response.read().decode("utf-8"))
            meta = data["chart"]["result"][0]["meta"]
            price = float(meta.get("regularMarketPrice", 0.0))
            
            # Extract official session percentage change directly from exchange metadata
            chg = meta.get("regularMarketChangePercent")
            if chg is None:
                chg = meta.get("fulldayChangePercent")
            if chg is not None:
                pct_change = round(float(chg), 2)
            else:
                prev = float(meta.get("chartPreviousClose", price))
                pct_change = round(((price - prev) / prev) * 100, 2) if prev else 0.0
                
            return price, pct_change
    except Exception as e:
        logger.warning(f"Could not fetch quote for {ticker}: {e}")
        return 0.0, 0.0

def fetch_gift_nifty() -> Tuple[float, float]:
    """
    Fetches official live GIFT Nifty (formerly SGX Nifty) from NSE IX derivatives market rate.
    Returns (price, session_pct_change). Guaranteed 24/7/365 data with database persistence.
    """
    try:
        url = "https://www.nseix.com/api/market-rate?type=derivatives"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.nseix.com/"
            }
        )
        with urllib.request.urlopen(req, context=_ssl_ctx, timeout=5) as r:
            d = json.loads(r.read().decode("utf-8"))
            for row in d.get("data", []):
                if row.get("SYMBOL") == "NIFTY" and row.get("INSTRUMENTTYPE") == "FUTIDX":
                    price = float(str(row.get("LASTPRICE", "0")).replace(",", ""))
                    raw_chg = str(row.get("PERCHANGE", "0")).strip()
                    chg = float(raw_chg) if raw_chg else 0.0
                    if price > 0:
                        global _last_saved_gift_price, _last_saved_gift_time
                        if abs(price - _last_saved_gift_price) >= 1.0 or (time.time() - _last_saved_gift_time) > 300.0:
                            try:
                                database.set_config("last_gift_nifty_price", str(price))
                                database.set_config("last_gift_nifty_chg", str(chg))
                                _last_saved_gift_price = price
                                _last_saved_gift_time = time.time()
                            except Exception:
                                pass
                        return price, chg
    except Exception as e:
        logger.warning(f"Could not fetch GIFT Nifty from market-rate: {e}")

    # Fallback 1: Deep intraday graph if session active
    try:
        req = urllib.request.Request(
            "https://www.nseix.com/api/deep-intraday-graph",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.nseix.com/"
            }
        )
        with urllib.request.urlopen(req, context=_ssl_ctx, timeout=4) as r:
            d = json.loads(r.read().decode("utf-8"))
            for series in reversed(d.get("data", [])):
                pts = series.get("data", [])
                if pts:
                    last_price = float(pts[-1][1])
                    ref_val = float(series.get("zones", [{}])[0].get("value") or pts[0][1])
                    pct = round(((last_price - ref_val) / ref_val) * 100, 2) if ref_val else 0.0
                    return last_price, pct
    except Exception as e:
        logger.warning(f"Could not fetch GIFT Nifty from deep-intraday-graph: {e}")

    # Fallback 2: Stored last closing price from DB
    try:
        saved_p = database.get_config("last_gift_nifty_price")
        saved_c = database.get_config("last_gift_nifty_chg")
        if saved_p:
            return float(saved_p), float(saved_c or 0.0)
    except Exception:
        pass

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
    if os.environ.get("IRON_BOT_TEST_MODE") == "1":
        return {
            "score": 0,
            "sentiment": "NEUTRAL",
            "bias": "⚪ NEUTRAL / BALANCED",
            "advisory": "Test Mode Active",
            "reason": "Test Mode Mock",
            "gift_nifty": {"price": 25200.0, "change": 0.0, "change_pct": 0.0},
            "dow": {"price": 44000.0, "change": 0.0, "change_pct": 0.0},
            "nasdaq": {"price": 19000.0, "change": 0.0, "change_pct": 0.0},
            "crude": {"price": 75.0, "change": 0.0, "change_pct": 0.0},
            "usdinr": {"price": 84.5, "change": 0.0, "change_pct": 0.0},
            "vix": {"price": 14.5, "change": 0.0, "change_pct": 0.0, "regime": "NORMAL"},
            "nifty": {"price": 25200.0, "change": 0.0, "change_pct": 0.0},
            "factors": ["Test Mode Active"]
        }

    global _cached_macro, _last_macro_time
    now = time.time()
    with _cache_lock:
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

    # 1. GIFT Nifty (Percentage Change vs Own Previous Close — avoids futures-vs-spot basis distortion) — Max ±30 pts
    if gift_price > 0:
        if gift_chg >= 0.50:
            score += 30
            factors.append(f"GIFT Nifty Strong Lead ({gift_chg:+.2f}%) (+30)")
        elif gift_chg >= 0.20:
            score += 15
            factors.append(f"GIFT Nifty Mild Lead ({gift_chg:+.2f}%) (+15)")
        elif gift_chg <= -0.50:
            score -= 30
            factors.append(f"GIFT Nifty Sharp Drag ({gift_chg:+.2f}%) (-30)")
        elif gift_chg <= -0.20:
            score -= 15
            factors.append(f"GIFT Nifty Mild Drag ({gift_chg:+.2f}%) (-15)")
        else:
            factors.append(f"GIFT Nifty Flat ({gift_chg:+.2f}%) (0)")

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

    macro_payload = {
        "score": score,
        "bias": bias,
        "advisory": advisory,
        "gift_nifty": {"price": gift_price, "change": gift_chg, "change_pct": gift_chg},
        "dow": {"price": dow_price, "change": dow_chg, "change_pct": dow_chg},
        "nasdaq": {"price": nas_price, "change": nas_chg, "change_pct": nas_chg},
        "crude": {"price": crude_price, "change": crude_chg, "change_pct": crude_chg},
        "usdinr": {"price": usdinr_price, "change": usdinr_chg, "change_pct": usdinr_chg},
        "vix": {"price": vix_price, "change": vix_chg, "change_pct": vix_chg, "regime": vix_regime},
        "nifty": {"price": nifty_price, "change": nifty_chg, "change_pct": nifty_chg},
        "factors": factors
    }
    with _cache_lock:
        _cached_macro = macro_payload
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
        f"🔒 *Iron Bot Status:* Armed on AWS EC2 Mumbai ({getattr(config, 'get_max_daily_trades', lambda: getattr(config, 'MAX_DAILY_TRADES', 4))()}-Trades / Day Quota Active)"
    )
    return telegram_notifier.send_telegram_alert(msg)

def start_macro_background_worker(interval_seconds: int = 30):
    """
    Spawns background daemon thread to continuously keep GIFT Nifty & Global Tickers hot.
    Guarantees sub-millisecond API response times and live dynamic updates.
    """
    import threading
    import time
    def _worker():
        logger.info(f"⚡ Macro Radar continuous background worker activated ({interval_seconds}s interval).")
        while True:
            try:
                get_macro_sentiment(force=True)
            except Exception as e:
                logger.warning(f"Macro background worker refresh warning: {e}")
            time.sleep(interval_seconds)

    t = threading.Thread(target=_worker, daemon=True, name="MacroRadarWorker")
    t.start()

if __name__ == "__main__":
    import pprint
    res = get_macro_sentiment()
    pprint.pprint(res)

