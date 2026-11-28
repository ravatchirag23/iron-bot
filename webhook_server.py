"""
Iron Bot Webhook Gateway & Live Dashboard Server
Zero-dependency HTTP server running on Python standard library.
Handles Webhooks from GoCharting, validates 13 Iron Armor Rules, and executes via Paper Engine.
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
import hmac
import html as html_lib
import json
import logging
import os
import re
import threading
import time
import urllib.parse
from datetime import datetime
from typing import Any, Dict, Optional
import config
import database
import risk_manager
import paper_engine
import telegram_notifier
import macro_radar
import option_chain_shield
import dhan_connector
import angel_connector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(os.path.dirname(__file__), "iron_bot.log")),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("IronBot.Server")

MAX_BODY_BYTES = 65536
_glitch_state: Dict[str, Any] = {"trade_id": None, "count": 0}
_feed_down_alert_state: Dict[str, float] = {"ts": 0.0}
_monitor_heartbeat: Dict[str, float] = {"ts": 0.0}


def get_health() -> Dict[str, Any]:
    """Returns component health summary (DB connectivity, monitor heartbeat, option feed, auth status)."""
    db_ok = False
    active_count = 0
    try:
        with database.db_session() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.execute("SELECT COUNT(*) FROM trades WHERE status = 'ACTIVE'")
            row = cur.fetchone()
            active_count = int(row[0]) if row else 0
            db_ok = True
    except Exception as e:
        logger.warning(f"Health check DB error: {e}")

    now_ts = time.time()
    last_beat = float(_monitor_heartbeat.get("ts") or 0.0)
    beat_age = round(now_ts - last_beat, 1) if last_beat > 0 else None
    poll_interval = float(getattr(config, "MONITOR_POLL_INTERVAL_SECONDS", 3))
    monitor_alive = bool(last_beat > 0 and beat_age is not None and beat_age < max(30.0, poll_interval * 5))

    cached_snap = getattr(option_chain_shield, "_cached_data", None)
    last_fetch = float(getattr(option_chain_shield, "_last_fetch_time", 0.0) or 0.0)
    feed_age = round(now_ts - last_fetch, 1) if last_fetch > 0 else None
    feed_ok = bool(
        cached_snap
        and not cached_snap.get("is_simulated", False)
        and feed_age is not None
        and feed_age <= float(getattr(config, "MAX_QUOTE_AGE_SECONDS", 20.0))
    )
    secret_configured = bool(getattr(config, "WEBHOOK_SECRET", "") or os.environ.get("IRON_BOT_SECRET", ""))

    return {
        "status": "OK" if db_ok else "DEGRADED",
        "db_ok": db_ok,
        "active_trades": active_count,
        "monitor_alive": monitor_alive,
        "monitor_beat_age_s": beat_age,
        "feed_ok": feed_ok,
        "feed_age_s": feed_age,
        "auth_configured": secret_configured,
        "paper_mode": bool(getattr(config, "PAPER_TRADING_MODE", True)),
        "allow_live_orders": bool(getattr(config, "ALLOW_LIVE_ORDERS", False)),
        "test_mode": os.environ.get("IRON_BOT_TEST_MODE") == "1",
        "timestamp": config.get_now_str_ist(),
    }


def monitor_tick(active: Optional[Dict[str, Any]] = None, now_ist: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """
    Executes one position monitor tick:
    - Updates monitor heartbeat
    - Enforces 15:15 IST mandatory square-off
    - Validates live option quote freshness (pauses on simulated/stale feed and rate-limits alert)
    - Uses live option spot price for +/-25 pt movement check and requires 3-tick persistence before suppressing jumps
    """
    _monitor_heartbeat["ts"] = time.time()
    now_ist = now_ist or config.get_now_ist()
    active = active if active is not None else database.get_active_trade()
    if not active:
        _glitch_state["trade_id"] = None
        _glitch_state["count"] = 0
        return None

    # B1. Mandatory 15:15 IST Square-Off Guardrail
    if now_ist.time() >= config.AUTO_SQUARE_OFF_TIME:
        logger.info(f"Mandatory 15:15 PM Square-off triggered for trade #{active['id']}.")
        return paper_engine.engine.force_exit(reason="15:15_PM_AUTO_SQUARE_OFF")

    symbol = active["symbol"]
    is_call = not risk_manager._is_put_signal(symbol)
    entry_spot = float(active.get("spot_price") or 25050.0)

    strike_match = re.search(r"NIFTY(\d+)", symbol)
    held_strike = int(strike_match.group(1)) if strike_match else round(entry_spot / 50.0) * 50

    mon_ttl = float(getattr(config, "MONITOR_QUOTE_TTL_SECONDS", 5.0))
    try:
        quote = option_chain_shield.get_option_quote_by_strike(held_strike, is_call, spot_price=entry_spot, max_age=mon_ttl)
    except TypeError:
        quote = option_chain_shield.get_option_quote_by_strike(held_strike, is_call, spot_price=entry_spot)

    live_ltp = float(quote.get("ltp") or 0.0)
    is_test_mode = os.environ.get("IRON_BOT_TEST_MODE") == "1"
    is_sim = bool(quote.get("is_simulated", False))
    is_stale = bool(quote.get("is_stale", False))
    has_error = bool(quote.get("error"))

    if has_error or live_ltp <= 0.0 or (not is_test_mode and (is_sim or is_stale)):
        reason_tag = quote.get("error") or ("simulated fallback" if is_sim else ("stale quote" if is_stale else "non-positive LTP"))
        logger.warning(f"Position monitor pausing tick for {symbol}: NSE feed unavailable ({reason_tag}).")
        now_ts = time.time()
        if (now_ts - float(_feed_down_alert_state.get("ts") or 0.0)) >= 300.0:
            _feed_down_alert_state["ts"] = now_ts
            telegram_notifier.send_telegram_alert(
                f"⚠️ *LIVE FEED UNAVAILABLE:* Option quote invalid ({reason_tag}) while holding `{symbol}`. Position monitor waiting for fresh tick."
            )
        return {"status": "FEED_DOWN_SKIPPED", "reason": reason_tag}

    live_spot = float(quote.get("spot") or entry_spot)
    entry_p = float(active.get("entry_price") or live_ltp)
    spot_mv = abs(live_spot - entry_spot)

    trade_id = active.get("id")
    if _glitch_state["trade_id"] != trade_id:
        _glitch_state["trade_id"] = trade_id
        _glitch_state["count"] = 0

    max_glitch = int(getattr(config, "GLITCH_MAX_CONSECUTIVE", 3))
    if abs(live_ltp - entry_p) > 25.0 and spot_mv < 25.0:
        _glitch_state["count"] += 1
        if _glitch_state["count"] < max_glitch:
            logger.warning(
                f"⚠️ Glitch tick {_glitch_state['count']}/{max_glitch} deferred: "
                f"live_ltp={live_ltp} vs entry={entry_p} (diff={live_ltp - entry_p:.1f} pts) while spot moved {spot_mv:.1f} pts."
            )
            return {"status": "GLITCH_DEFERRED", "count": _glitch_state["count"], "ltp": live_ltp}
        logger.warning(
            f"Persistent move confirmed after {_glitch_state['count']} ticks at ₹{live_ltp} (spot_mv={spot_mv:.1f}); forwarding tick."
        )
    else:
        _glitch_state["count"] = 0

    res = paper_engine.engine.update_tick(live_ltp)
    if res and res.get("status") == "CLOSED":
        logger.info(f"Position #{trade_id} closed automatically: {res.get('reason')} at ₹{live_ltp}")
    return res


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class WebhookHandler(BaseHTTPRequestHandler):
    def _send_json(self, status_code: int, data: dict):
        response = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def _send_html(self, status_code: int, html_str: str):
        response = html_str.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def _clean_path(self) -> str:
        return self.path.split("?", 1)[0]

    def _query_params(self) -> Dict[str, list]:
        if "?" not in self.path:
            return {}
        return urllib.parse.parse_qs(self.path.split("?", 1)[1])

    def _guard(self, is_webhook: bool = False, body_payload: Optional[Dict[str, Any]] = None) -> bool:
        """Enforces constant-time shared secret verification when IRON_BOT_SECRET / WEBHOOK_SECRET is configured."""
        secret = str(getattr(config, "WEBHOOK_SECRET", "") or os.environ.get("IRON_BOT_SECRET", "")).strip()
        if not secret:
            return True

        provided = (
            self.headers.get("X-Iron-Secret")
            or self.headers.get("X-Webhook-Secret")
            or ""
        ).strip()

        if not provided:
            auth_hdr = (self.headers.get("Authorization") or "").strip()
            if auth_hdr.lower().startswith("bearer "):
                provided = auth_hdr[7:].strip()

        if not provided and isinstance(body_payload, dict):
            provided = str(body_payload.get("secret") or body_payload.get("webhook_secret") or "").strip()

        if not provided:
            qs = self._query_params()
            if "secret" in qs and qs["secret"]:
                provided = str(qs["secret"][0]).strip()
            elif "key" in qs and qs["key"]:
                provided = str(qs["key"][0]).strip()

        if not provided or not hmac.compare_digest(provided, secret):
            self._send_json(401, {"error": "Unauthorized: invalid or missing X-Iron-Secret"})
            return False
        return True

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    def do_GET(self):
        path = self._clean_path()
        if path == "/" or path == "/status":
            self.handle_status()
        elif path == "/health":
            health = get_health()
            self._send_json(200 if health.get("db_ok") else 503, health)
        elif path == "/api/live_state":
            import dashboard
            state = dashboard.get_live_dashboard_state()
            self._send_json(200, state)
        elif path == "/api/refresh_macro":
            data = macro_radar.get_macro_sentiment(force=True)
            self._send_json(200, data)
        elif path == "/api/candles":
            params = self._query_params()
            symbol = params.get("symbol", ["^NSEI"])[0]
            range_param = params.get("range", ["2d"])[0]
            interval = params.get("interval", ["5m"])[0]
            candles = macro_radar.fetch_candles(symbol, interval, range_param)
            self._send_json(200, {"symbol": symbol, "candles": candles})
        elif path == "/trades":
            trades = database.get_all_trades(50)
            self._send_json(200, {"trades": trades})
        elif path in ("/test_telegram", "/api/test_telegram"):
            if not self._guard():
                return
            ok = telegram_notifier.send_telegram_alert("🚀 *IRON BOT:* Live Telegram Connection Verified on AWS Cloud (Sydney)!")
            self._send_json(200, {"telegram_sent": ok})
        elif path == "/macro":
            if not self._guard():
                return
            data = macro_radar.get_macro_sentiment()
            macro_radar.send_morning_briefing()
            self._send_json(200, data)
        elif path == "/option_chain":
            data = option_chain_shield.get_oi_snapshot()
            self._send_json(200, data)
        elif path == "/dhan":
            data = dhan_connector.get_connection_status()
            self._send_json(200, data)
        elif path == "/handshake":
            dhan_res = dhan_connector.run_8am_handshake(force=True)
            angel_res = angel_connector.run_8am_handshake(force=True)
            combined = dict(dhan_res) if isinstance(dhan_res, dict) else {}
            combined["dhan"] = dhan_res
            combined["angel"] = angel_res
            self._send_json(200, combined)
        elif path == "/update_token":
            self.handle_update_token_page()
        else:
            self._send_json(404, {"error": "Endpoint not found"})

    def do_POST(self):
        raw_len = self.headers.get("Content-Length", "0")
        try:
            content_length = int(raw_len)
        except (ValueError, TypeError):
            self._send_json(400, {"error": "Invalid Content-Length header"})
            return

        if content_length < 0:
            self._send_json(400, {"error": "Negative Content-Length"})
            return
        if content_length > MAX_BODY_BYTES:
            self._send_json(413, {"error": f"Payload too large (max {MAX_BODY_BYTES} bytes)"})
            return

        post_data = self.rfile.read(content_length) if content_length > 0 else b""
        raw_str = post_data.decode("utf-8", errors="ignore")

        try:
            payload = json.loads(raw_str) if post_data else {}
            if not isinstance(payload, dict):
                payload = {"value": payload}
        except Exception:
            if post_data and "=" in raw_str and not raw_str.lstrip().startswith("{"):
                parsed_form = urllib.parse.parse_qs(raw_str)
                payload = {k: (v[0] if len(v) == 1 else v) for k, v in parsed_form.items()}
                payload["raw_text"] = raw_str
            else:
                payload = {"raw_text": raw_str}

        path = self._clean_path()
        if path == "/webhook":
            if not self._guard(is_webhook=True, body_payload=payload):
                return
            self.handle_webhook(payload, internal=False)
        elif path == "/simulate":
            if not self._guard(body_payload=payload):
                return
            self.handle_simulate(payload)
        elif path == "/tick":
            if not self._guard(body_payload=payload):
                return
            self.handle_tick(payload)
        elif path == "/reset":
            if not self._guard(body_payload=payload):
                return
            self.handle_reset()
        elif path == "/reset_wallet":
            if not self._guard(body_payload=payload):
                return
            self.handle_reset_wallet()
        elif path in ("/test_telegram", "/api/test_telegram"):
            if not self._guard(body_payload=payload):
                return
            ok = telegram_notifier.send_telegram_alert("🚀 *IRON BOT:* Live Telegram Connection Verified on AWS Cloud (Sydney)!")
            self._send_json(200, {"telegram_sent": ok})
        elif path in ("/api/refresh_macro", "/api/macro_refresh"):
            if not self._guard(body_payload=payload):
                return
            macro_radar.get_macro_sentiment(force=True)
            self._send_json(200, {"status": "SUCCESS", "message": "Macro radar cache refreshed"})
        elif path == "/update_token":
            if not self._guard(body_payload=payload):
                return
            self.handle_update_token_post(post_data)
        else:
            self._send_json(404, {"error": "Endpoint not found"})

    def handle_webhook(self, payload: dict, internal: bool = False):
        logger.info(f"Incoming Webhook Alert: {payload}")

        raw_sig = str(
            payload.get("signal")
            or payload.get("message")
            or payload.get("action")
            or payload.get("raw_text")
            or ""
        ).upper()

        # Check EXIT BEFORE direction parsing so EXIT never falls through to BUY_CE
        is_exit_cmd = ("EXIT" in raw_sig) or ("CLOSE" in raw_sig and "CANDLE" not in raw_sig)
        if is_exit_cmd:
            with paper_engine.engine.lock:
                active = database.get_active_trade()
                if not active:
                    self._send_json(200, {"status": "NO_ACTIVE_TRADE", "message": "No active position to exit"})
                    return
                raw_exit = payload.get("entry_price")
                exit_px: Optional[float] = None
                if raw_exit is not None:
                    try:
                        val = float(raw_exit)
                        if 0.0 < val <= 5000.0:
                            exit_px = val
                    except (ValueError, TypeError):
                        exit_px = None
                raw_spot_hint = payload.get("spot") or payload.get("close") or payload.get("price")
                spot_hint: Optional[float] = None
                if raw_spot_hint is not None:
                    try:
                        spot_hint = float(raw_spot_hint)
                    except (ValueError, TypeError):
                        spot_hint = None
                res = paper_engine.engine.force_exit(exit_px, "MANUAL_EXIT_COMMAND", current_spot=spot_hint)
                self._send_json(200, {"status": "EXIT_EXECUTED", "details": res})
                return

        # Accurate Direction Parsing via word-boundary regex (reject unknown/garbage signals)
        if risk_manager._is_put_signal(raw_sig):
            signal_type = "BUY_PE"
        elif risk_manager._is_call_signal(raw_sig):
            signal_type = "BUY_CE"
        else:
            logger.warning(f"Rejected unrecognized webhook signal: {raw_sig!r}")
            self._send_json(400, {"status": "REJECTED", "reason": f"Unrecognized signal action: {raw_sig or 'EMPTY'}"})
            return

        raw_spot = payload.get("spot") or payload.get("close") or payload.get("price")
        try:
            spot_price = float(raw_spot) if raw_spot is not None else 25050.0
        except (ValueError, TypeError):
            spot_price = 25050.0

        raw_entry = payload.get("entry_price")
        try:
            entry_price = float(raw_entry) if raw_entry is not None else None
        except (ValueError, TypeError):
            entry_price = None

        signal_data: Dict[str, Any] = {
            "signal": signal_type,
            "spot": spot_price,
            "entry_price": entry_price,
            "day_open": payload.get("day_open"),
            "candle_high": payload.get("candle_high"),
            "candle_low": payload.get("candle_low"),
            "candle_close": payload.get("candle_close"),
            "candle_range": float(payload.get("candle_range") or 15.0),
            "atr": payload.get("atr"),
            "pullback_reentry": bool(payload.get("pullback_reentry", False)),
            "is_test": bool(payload.get("is_test", False)) if internal else False,
        }

        # Log arrival without triggering self-debounce (debounce checks SIGNAL_ACCEPTED_* / SIGNAL_RECEIVED_*)
        database.log_event(f"SIGNAL_RAW_{signal_type}", json.dumps(payload))

        with paper_engine.engine.lock:
            active = database.get_active_trade()
            if active:
                active_is_ce = not risk_manager._is_put_signal(active["symbol"])
                incoming_is_pe = risk_manager._is_put_signal(signal_type)
                is_opposite = (active_is_ce and incoming_is_pe) or (not active_is_ce and not incoming_is_pe)
                if is_opposite:
                    logger.info(
                        f"Opposite signal {signal_type} received while holding #{active['id']} {active['symbol']}. Closing old position for reversal!"
                    )
                    paper_engine.engine.force_exit(exit_price=None, reason="INDICATOR_OPPOSITE_SIGNAL", current_spot=spot_price)

            # Iron Armor Rules Validation
            is_approved, reason = risk_manager.validate_signal_armor(signal_data)
            if not is_approved:
                logger.warning(f"Trade Blocked by Armor: {reason}")
                telegram_notifier.notify_rejection(reason, signal_data)
                self._send_json(400, {"status": "REJECTED", "reason": reason})
                return

            try:
                trade_params = risk_manager.calculate_trade_parameters(signal_data)
            except risk_manager.PremiumUnavailableError as e:
                reason = f"NO_LIVE_PREMIUM: {e}"
                logger.warning(f"Trade Blocked (Feed Unavailable): {reason}")
                telegram_notifier.notify_rejection(reason, signal_data)
                self._send_json(400, {"status": "REJECTED", "reason": reason})
                return

            oi_advisory = ""
            if getattr(config, "ENABLE_OI_SHIELD", True):
                try:
                    is_call = not risk_manager._is_put_signal(trade_params["signal_type"])
                    strike = risk_manager.get_atm_strike(trade_params["spot_price"], is_call)
                    oi_eval = option_chain_shield.evaluate_signal_confluence(
                        trade_params["signal_type"],
                        trade_params["spot_price"],
                        strike
                    )
                    oi_advisory = oi_eval.get("advisory", "")

                    if getattr(config, "ENFORCE_OI_HARD_FILTER", False) and not oi_eval.get("has_confluence", True):
                        reason = f"Option Chain Wall Block: {oi_advisory}"
                        logger.warning(reason)
                        telegram_notifier.notify_rejection(reason, signal_data)
                        self._send_json(400, {"status": "REJECTED", "reason": reason})
                        return
                except Exception as e:
                    logger.warning(f"Option shield advisory bypassed safely: {e}")
                    oi_advisory = ""

            database.log_event(f"SIGNAL_ACCEPTED_{signal_type}", json.dumps(payload))
            trade_id = paper_engine.engine.process_entry(trade_params, oi_advisory=oi_advisory)
            if trade_id <= 0:
                self._send_json(409, {"status": "REJECTED", "reason": "Concurrent active trade blocked"})
                return

        self._send_json(200, {
            "status": "APPROVED_AND_EXECUTED",
            "trade_id": trade_id,
            "trade_params": trade_params,
            "paper_mode": config.PAPER_TRADING_MODE
        })

    def handle_tick(self, payload: dict):
        """Simulate real-time tick to test target or SL movement."""
        price = float(payload.get("price", 58.0))
        res = paper_engine.engine.update_tick(price)
        self._send_json(200, {"tick_result": res})

    def handle_simulate(self, payload: dict):
        """Simulate an end-to-end signal trigger for testing."""
        signal = payload.get("signal", "BUY_CE")
        spot = float(payload.get("spot", 23820.0))
        entry_price = float(payload.get("entry_price", 58.0))
        candle_low = float(payload.get("candle_low", 52.0))

        sim_payload = {
            "signal": signal,
            "spot": spot,
            "entry_price": entry_price,
            "candle_low": candle_low,
            "is_test": True
        }
        self.handle_webhook(sim_payload, internal=True)

    def handle_reset(self):
        """Resets today's tracker for fresh testing."""
        today_str = config.get_today_ist()
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM daily_tracker WHERE trade_date = ?", (today_str,))
        cursor.execute("UPDATE trades SET status = 'CLOSED' WHERE status = 'ACTIVE'")
        conn.commit()
        conn.close()
        paper_engine.engine.sync_state()
        self._send_json(200, {"status": "RESET_SUCCESSFUL", "message": "Daily limits & active trades cleared for testing"})

    def handle_reset_wallet(self):
        """Initializes or resets the 30-Day ₹50K campaign starting today."""
        today_str = config.get_today_ist()
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM trades WHERE trade_date = ?", (today_str,))
        cursor.execute("DELETE FROM daily_tracker WHERE trade_date = ?", (today_str,))
        conn.commit()
        conn.close()
        database.init_campaign_wallet(initial_capital=config.INITIAL_WALLET_CAPITAL, start_date=today_str)
        paper_engine.engine.sync_state()
        logger.info(f"30-Day Campaign initialized with ₹{config.INITIAL_WALLET_CAPITAL:,.2f} INR wallet cap on {today_str}")
        self._send_json(200, {
            "status": "WALLET_RESET_SUCCESSFUL",
            "message": f"30-Day Campaign initialized with strict ₹{config.INITIAL_WALLET_CAPITAL:,.2f} INR wallet cap starting {today_str}!"
        })

    def handle_status(self):
        try:
            import dashboard
            html = dashboard.render_dashboard_html()
            self._send_html(200, html)
        except Exception as e:
            logger.error(f"Dashboard render exception: {e}")
            self._send_json(500, {"error": "Dashboard render failed", "detail": str(e)})

    def handle_update_token_page(self, notice: str = ""):
        status = dhan_connector.get_connection_status()
        notice_html = f"<div style='background: #27ae60; color: white; padding: 12px; border-radius: 8px; margin-bottom: 20px; font-weight: bold;'>{notice}</div>" if notice else ""
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>DhanHQ Token Renewal — Iron Bot</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 20px; margin: 0; }}
                .container {{ max-width: 600px; margin: auto; background: #1e293b; padding: 25px; border-radius: 12px; border: 1px solid #334155; }}
                h2 {{ margin-top: 0; color: #38bdf8; }}
                .status-box {{ background: #0f172a; padding: 15px; border-radius: 8px; margin-bottom: 20px; font-size: 14px; border-left: 4px solid #38bdf8; line-height: 1.8; }}
                textarea {{ width: 100%; box-sizing: border-box; height: 110px; background: #0f172a; color: #f8fafc; border: 1px solid #475569; border-radius: 6px; padding: 10px; font-family: monospace; font-size: 12px; }}
                button {{ width: 100%; padding: 14px; background: #2563eb; color: white; border: none; border-radius: 6px; font-size: 16px; font-weight: bold; cursor: pointer; margin-top: 15px; }}
                button:hover {{ background: #1d4ed8; }}
                .instructions {{ font-size: 13px; color: #94a3b8; margin-top: 20px; line-height: 1.6; }}
                a {{ color: #38bdf8; text-decoration: none; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h2>⚡ DhanHQ 24h Token Renewal</h2>
                {notice_html}
                <div class="status-box">
                    <div><b>Client ID:</b> {status.get('client_id')}</div>
                    <div><b>Token Validity:</b> {status.get('token_validity')}</div>
                    <div><b>Status:</b> {'🟢 CONNECTED' if status.get('connected') else '🔴 EXPIRED'}</div>
                    <div><b>Canary Execution:</b> 🛡️ 100% Virtual Mode Locked</div>
                </div>

                <form action="/update_token" method="POST">
                    <label style="font-size: 14px; font-weight: bold; display: block; margin-bottom: 8px;">Admin Secret (X-Iron-Secret):</label>
                    <input type="password" name="secret" placeholder="Enter IRON_BOT_SECRET" style="width:100%;box-sizing:border-box;background:#0f172a;color:#f8fafc;border:1px solid #475569;border-radius:6px;padding:10px;margin-bottom:12px;" />
                    <label style="font-size: 14px; font-weight: bold; display: block; margin-bottom: 8px;">Paste New Access Token:</label>
                    <textarea name="token" placeholder="eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9..." required></textarea>
                    <button type="submit">Update Token & Verify Handshake</button>
                </form>

                <div class="instructions">
                    <b>Quick Steps (15 Seconds):</b><br>
                    1. Open <a href="https://web.dhan.co" target="_blank">web.dhan.co</a> on your phone/laptop.<br>
                    2. Go to <b>My Profile</b> ➔ <b>Access DhanHQ APIs</b>.<br>
                    3. Click <b>Generate new Access Token</b> and copy it.<br>
                    4. Paste above and click <b>Update Token</b>.<br>
                    <i>Instant live update in memory & DB — zero Render restart needed!</i>
                </div>
                <div style="margin-top: 15px; text-align: center;">
                    <a href="/status">← Back to Dashboard</a>
                </div>
            </div>
        </body>
        </html>
        """
        self._send_html(200, html)

    def handle_update_token_post(self, post_data: bytes):
        import urllib.parse
        raw_str = post_data.decode("utf-8", errors="ignore")
        
        new_token = ""
        try:
            parsed_json = json.loads(raw_str)
            new_token = parsed_json.get("token", "")
        except Exception:
            parsed_form = urllib.parse.parse_qs(raw_str)
            tokens = parsed_form.get("token", [])
            if tokens:
                new_token = tokens[0]

        res = dhan_connector.update_access_token(new_token)
        if res.get("success"):
            notice = f"✅ Token Successfully Updated! Valid until: {res.get('validity')}"
            self.handle_update_token_page(notice=notice)
        else:
            self._send_json(400, res)

def start_position_monitor_thread():
    """
    Background daemon:
    1. Monitors active trade every 3s (Gap #1 Protection) via monitor_tick().
    2. Runs Daily 8:00 AM IST Automated Pre-Market Handshake.
    """
    def monitor_loop():
        import time as time_lib
        logger.info("⚡ Background Daemon activated (Position Monitor + 8:00 AM Handshake).")
        last_8am_date = None
        while True:
            try:
                now_ist = config.get_now_ist()
                today_str = now_ist.strftime("%Y-%m-%d")
                if now_ist.hour == 8 and last_8am_date != today_str:
                    logger.info("[DAEMON] Triggering 8:00 AM IST Pre-Market Handshake & Macro Briefing...")
                    try:
                        angel_connector.run_8am_handshake()
                    except Exception as e:
                        logger.error(f"Angel handshake error: {e}")
                    try:
                        macro_radar.send_morning_briefing()
                    except Exception as e:
                        logger.error(f"Macro radar briefing error: {e}")
                    last_8am_date = today_str

                monitor_tick(now_ist=now_ist)
            except Exception as e:
                logger.warning(f"Background Daemon loop exception (recovered safely): {e}")
            finally:
                time_lib.sleep(getattr(config, "MONITOR_POLL_INTERVAL_SECONDS", 3))

    t = threading.Thread(target=monitor_loop, daemon=True)
    t.start()

def start_server():
    start_position_monitor_thread()
    
    # Start Continuous Global Macro Radar Worker (30s background polling)
    try:
        import macro_radar
        macro_radar.start_macro_background_worker(interval_seconds=30)
        logger.info("⚡ Continuous Global Macro Radar worker started successfully (30s refresh)!")
    except Exception as e:
        logger.warning(f"Macro worker startup exception: {e}")

    # Start Autonomous Native Market Scanner (Zero-Dependency In-House Engine)
    try:
        import native_scanner
        native_scanner.scanner.start_scanner_thread()
        logger.info("⚡ Native UT Bot Autonomous Scanner started successfully!")
    except Exception as e:
        logger.warning(f"Native scanner startup exception: {e}")

    server = ThreadingHTTPServer((config.HOST, config.PORT), WebhookHandler)
    logger.info(f"Iron Bot Webhook Gateway running at http://localhost:{config.PORT}")
    logger.info(f"Dashboard available at http://localhost:{config.PORT}/status")
    logger.info(f"Paper Trading Mode: {config.PAPER_TRADING_MODE} (100% Safe)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Iron Bot Server shutting down cleanly.")
        server.server_close()

if __name__ == "__main__":
    start_server()
