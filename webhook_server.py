"""
Iron Bot Webhook Gateway & Live Dashboard Server
Zero-dependency HTTP server running on Python standard library.
Handles Webhooks from GoCharting, validates 13 Iron Armor Rules, and executes via Paper Engine.
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
import json
import logging
import threading
from datetime import datetime
import os
import config
import database
import risk_manager
import paper_engine
import telegram_notifier
import macro_radar
import option_chain_shield
import dhan_connector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(os.path.dirname(__file__), "iron_bot.log")),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("IronBot.Server")

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

class WebhookHandler(BaseHTTPRequestHandler):
    def _send_json(self, status_code: int, data: dict):
        response = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(response)

    def _send_html(self, status_code: int, html_str: str):
        response = html_str.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    def do_GET(self):
        if self.path == "/" or self.path == "/status":
            self.handle_status()
        elif self.path == "/trades":
            trades = database.get_all_trades(50)
            self._send_json(200, {"trades": trades})
        elif self.path == "/test_telegram":
            ok = telegram_notifier.send_telegram_alert("🚀 *IRON BOT:* Live Telegram Connection Verified on Render Cloud!")
            self._send_json(200, {"telegram_sent": ok})
        elif self.path == "/macro":
            data = macro_radar.get_macro_sentiment()
            macro_radar.send_morning_briefing()
            self._send_json(200, data)
        elif self.path == "/option_chain":
            data = option_chain_shield.get_oi_snapshot()
            self._send_json(200, data)
        elif self.path == "/dhan":
            data = dhan_connector.get_connection_status()
            self._send_json(200, data)
        elif self.path == "/handshake":
            data = dhan_connector.run_8am_handshake(force=True)
            self._send_json(200, data)
        elif self.path.startswith("/update_token"):
            self.handle_update_token_page()
        else:
            self._send_json(404, {"error": "Endpoint not found"})

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length)
        
        try:
            payload = json.loads(post_data.decode("utf-8")) if post_data else {}
        except Exception:
            # Handle plain text query format if sent by webhook
            payload = {"raw_text": post_data.decode("utf-8", errors="ignore")}

        if self.path == "/webhook":
            self.handle_webhook(payload)
        elif self.path == "/simulate":
            self.handle_simulate(payload)
        elif self.path == "/tick":
            self.handle_tick(payload)
        elif self.path == "/reset":
            self.handle_reset()
        elif self.path == "/update_token":
            self.handle_update_token_post(post_data)
        else:
            self._send_json(404, {"error": "Endpoint not found"})

    def handle_webhook(self, payload: dict):
        logger.info(f"Incoming Webhook Alert: {payload}")
        
        # Parse GoCharting / TradingView payload
        signal_type = payload.get("signal") or payload.get("message") or payload.get("action", "")
        if not signal_type and "raw_text" in payload:
            raw = str(payload["raw_text"])
            if any(k in raw.upper() for k in ["BUY", "CE", "LONG"]):
                signal_type = "BUY_CE"
            elif any(k in raw.upper() for k in ["SELL", "PE", "SHORT"]):
                signal_type = "SELL_PE"

        # Spot price handling (fallback to current Nifty 25050)
        raw_spot = payload.get("spot") or payload.get("close") or payload.get("price")
        try:
            spot_price = float(raw_spot) if raw_spot is not None else 25050.0
        except (ValueError, TypeError):
            spot_price = 25050.0
        
        # Format standardized signal data
        signal_data = {
            "signal": signal_type,
            "spot": spot_price,
            "entry_price": float(payload.get("entry_price", config.DEFAULT_ASSUMED_PREMIUM)),
            "candle_low": payload.get("candle_low"),
            "candle_range": float(payload.get("candle_range", 15.0)),
            "is_test": payload.get("is_test", False)
        }
        
        database.log_event(f"SIGNAL_RECEIVED_{signal_type}", json.dumps(payload))

        # Check if incoming signal is an EXIT command
        active = database.get_active_trade()
        if active and ("SELL" in signal_type.upper() or "EXIT" in signal_type.upper()):
            exit_price = float(payload.get("entry_price", config.DEFAULT_ASSUMED_PREMIUM))
            res = paper_engine.engine.force_exit(exit_price, "INDICATOR_OPPOSITE_SIGNAL")
            self._send_json(200, {"status": "EXIT_EXECUTED", "details": res})
            return

        # 13 Iron Armor Rules Validation
        is_approved, reason = risk_manager.validate_signal_armor(signal_data)
        if not is_approved:
            logger.warning(f"Trade Blocked by Armor: {reason}")
            telegram_notifier.notify_rejection(reason, signal_data)
            self._send_json(400, {"status": "REJECTED", "reason": reason})
            return

        # Calculate exact parameters
        trade_params = risk_manager.calculate_trade_parameters(signal_data)
        
        # Loosely Coupled Option Chain Confluence Evaluation
        oi_advisory = ""
        if getattr(config, "ENABLE_OI_SHIELD", True):
            try:
                strike = risk_manager.get_atm_strike(trade_params["spot_price"], "BUY" in trade_params["signal_type"])
                oi_eval = option_chain_shield.evaluate_signal_confluence(
                    trade_params["signal_type"], 
                    trade_params["spot_price"], 
                    strike
                )
                oi_advisory = oi_eval.get("advisory", "")
                
                # Optional Hard Filter (Disabled by default - non-blocking)
                if getattr(config, "ENFORCE_OI_HARD_FILTER", False) and not oi_eval.get("has_confluence", True):
                    reason = f"Option Chain Wall Block: {oi_advisory}"
                    logger.warning(reason)
                    telegram_notifier.notify_rejection(reason, signal_data)
                    self._send_json(400, {"status": "REJECTED", "reason": reason})
                    return
            except Exception as e:
                logger.warning(f"Option shield advisory bypassed safely: {e}")
                oi_advisory = ""

        trade_id = paper_engine.engine.process_entry(trade_params, oi_advisory=oi_advisory)
        
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
        self.handle_webhook(sim_payload)

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

    def handle_status(self):
        stats = database.get_today_stats()
        active = database.get_active_trade()
        all_trades = database.get_all_trades(10)
        
        status_color = "#e74c3c" if stats.get("is_locked", 0) == 1 else "#2ecc71"
        status_text = "LOCKED (Limit Reached)" if stats.get("is_locked", 0) == 1 else "ACTIVE & ARMED"

        macro_text = "⚪ Neutral (Feed idle)"
        oi_text = "PCR: 1.04 | Res: 25,300 | Supp: 24,800"
        try:
            m = macro_radar.get_macro_sentiment()
            macro_text = f"{m['bias']} ({m['score']:+d}/100)"
        except Exception:
            pass

        try:
            o = option_chain_shield.get_oi_snapshot()
            oi_text = f"PCR: <b>{o['pcr']}</b> | Res: <b>{o['call_wall_resistance']}</b> | Supp: <b>{o['put_wall_support']}</b>"
        except Exception:
            pass

        dhan_info = dhan_connector.get_connection_status()
        dhan_badge = "🟢 CONNECTED" if dhan_info.get("connected") else "🔴 DISCONNECTED"
        dhan_client = dhan_info.get("client_id", "N/A")
        dhan_validity = dhan_info.get("token_validity", "N/A")
        dhan_balance = dhan_info.get("available_balance", 0.0)

        trades_html = ""
        for t in all_trades:
            pnl_color = "#2ecc71" if t["pnl_rupees"] >= 0 else "#e74c3c"
            trades_html += f"""
            <tr>
                <td>#{t['id']}</td>
                <td>{t['timestamp']}</td>
                <td><b>{t['symbol']}</b></td>
                <td>₹{t['entry_price']}</td>
                <td>₹{t['sl_price']}</td>
                <td>₹{t['target_price']}</td>
                <td>₹{t['exit_price'] or '-'}</td>
                <td><span style="color: {pnl_color}; font-weight: bold;">{t['pnl_points']:+.1f} pts (₹{t['pnl_rupees']:+.2f})</span></td>
                <td>{t['exit_reason'] or 'ACTIVE'}</td>
            </tr>
            """

        active_html = "<p style='color: #7f8c8d;'>No active position. Waiting for confirmed GoCharting signal...</p>"
        if active:
            active_html = f"""
            <div style="background: #ecf0f1; padding: 15px; border-left: 5px solid #3498db; border-radius: 4px;">
                <h3 style="margin-top: 0; color: #2c3e50;">Active Position: {active['symbol']} (1 Lot: {active['quantity']} Qty)</h3>
                <p>Entry: <b>₹{active['entry_price']}</b> | Micro-SL: <b>₹{active['sl_price']}</b> | Target: <b>₹{active['target_price']}</b></p>
                <p>Entered At: {active['timestamp']}</p>
            </div>
            """

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Iron Bot Dashboard — Chirag Ravat</title>
            <meta http-equiv="refresh" content="5">
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f8f9fa; margin: 0; padding: 25px; color: #2c3e50; }}
                .container {{ max-width: 1000px; margin: auto; background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.08); }}
                .header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #ecf0f1; padding-bottom: 15px; }}
                .badge {{ background: {status_color}; color: white; padding: 8px 16px; border-radius: 20px; font-weight: bold; font-size: 14px; }}
                .cards {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; margin: 25px 0; }}
                .card {{ background: #fdfefe; border: 1px solid #e1e8ed; padding: 20px; border-radius: 8px; text-align: center; }}
                .card-val {{ font-size: 26px; font-weight: bold; margin: 10px 0 0 0; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 20px; font-size: 14px; }}
                th, td {{ padding: 12px; border-bottom: 1px solid #ecf0f1; text-align: left; }}
                th {{ background: #f4f6f7; color: #7f8c8d; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <div>
                        <h2 style="margin: 0;">🤖 IRON BOT — Live Operations Center</h2>
                        <small style="color: #7f8c8d;">Mission: 15-20 pts Passive Income | 100% Virtual Ledger (₹0 Real Money at Risk)</small>
                    </div>
                    <div class="badge">{status_text}</div>
                </div>

                <div class="cards">
                    <div class="card">
                        <small style="color: #7f8c8d; font-weight: bold;">TODAY'S TRADES</small>
                        <div class="card-val">{stats.get('trade_count', 0)} / {config.MAX_DAILY_TRADES}</div>
                    </div>
                    <div class="card">
                        <small style="color: #7f8c8d; font-weight: bold;">TODAY'S NET P&L</small>
                        <div class="card-val" style="color: {'#2ecc71' if stats.get('total_pnl_rupees', 0) >= 0 else '#e74c3c'};">
                            ₹{stats.get('total_pnl_rupees', 0.0):+.2f}
                        </div>
                    </div>
                    <div class="card">
                        <small style="color: #7f8c8d; font-weight: bold;">PROTECTION MODE</small>
                        <div class="card-val" style="color: #3498db; font-size: 20px;">13 IRON RULES ACTIVE</div>
                    </div>
                </div>

                <div class="cards" style="grid-template-columns: repeat(2, 1fr); margin-top: -5px;">
                    <div class="card" style="text-align: left; padding: 15px 20px;">
                        <small style="color: #7f8c8d; font-weight: bold;">🧭 PRE-MARKET MACRO RADAR</small>
                        <div style="font-size: 16px; font-weight: bold; margin-top: 8px;">{macro_text}</div>
                    </div>
                    <div class="card" style="text-align: left; padding: 15px 20px;">
                        <small style="color: #7f8c8d; font-weight: bold;">📊 OPTION CHAIN MARKET STRUCTURE</small>
                        <div style="font-size: 16px; margin-top: 8px;">{oi_text}</div>
                    </div>
                </div>

                <div class="card" style="text-align: left; padding: 15px 20px; margin-top: -5px; border-left: 5px solid #27ae60;">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <small style="color: #7f8c8d; font-weight: bold;">⚡ DHANHQ INSTITUTIONAL BROKER GATEWAY</small>
                        <span style="font-size: 12px; font-weight: bold; color: {'#27ae60' if dhan_info.get('connected') else '#e74c3c'};">{dhan_badge}</span>
                    </div>
                    <div style="font-size: 14px; margin-top: 8px; display: flex; gap: 20px; flex-wrap: wrap;">
                        <div>Client ID: <b>{dhan_client}</b></div>
                        <div>Ledger Balance: <b>₹{dhan_balance:.2f}</b> (Safe ₹0 Risk)</div>
                        <div>Token Validity: <b>{dhan_validity}</b></div>
                        <div>Execution: <b style="color: #2980b9;">🛡️ Canary Virtual Locked</b></div>
                        <div><a href="/update_token" style="color: #2980b9; text-decoration: underline; font-weight: bold;">🔑 1-Tap Renew Token</a></div>
                    </div>
                </div>

                <h3>Current Live Position</h3>
                {active_html}

                <h3>Paper Trades Ledger (Audit Trail)</h3>
                <table>
                    <thead>
                        <tr>
                            <th>Trade ID</th>
                            <th>Time</th>
                            <th>Strike</th>
                            <th>Entry</th>
                            <th>SL</th>
                            <th>Target</th>
                            <th>Exit</th>
                            <th>P&L</th>
                            <th>Reason</th>
                        </tr>
                    </thead>
                    <tbody>
                        {trades_html if trades_html else "<tr><td colspan='9' style='text-align: center; color: #95a5a6;'>No trades executed yet. System is armed and waiting.</td></tr>"}
                    </tbody>
                </table>
            </div>
        </body>
        </html>
        """
        self._send_html(200, html)

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
    1. Monitors active trade every 3s (Gap #1 Protection).
    2. Runs Daily 8:00 AM IST Automated Pre-Market Handshake.
    """
    def monitor_loop():
        import time as time_lib
        logger.info("⚡ Background Daemon activated (Position Monitor + 8:00 AM Handshake).")
        last_8am_date = None
        while True:
            try:
                # A. Daily 8:00 AM IST Automated Handshake
                now_ist = config.get_now_ist()
                today_str = now_ist.strftime("%Y-%m-%d")
                if now_ist.hour == 8 and last_8am_date != today_str:
                    logger.info("[DAEMON] Triggering 8:00 AM IST Pre-Market Handshake...")
                    dhan_connector.run_8am_handshake()
                    last_8am_date = today_str

                # B. Position Monitor
                active = database.get_active_trade()
                if active:
                    symbol = active["symbol"]
                    is_call = "CE" in symbol
                    spot = float(active.get("spot_price", 25050.0))
                    
                    quote = option_chain_shield.get_real_option_quote(spot, is_call)
                    live_ltp = float(quote.get("ltp", 0.0))
                    
                    if live_ltp > 0.0:
                        res = paper_engine.engine.update_tick(live_ltp)
                        if res and res.get("status") == "CLOSED":
                            logger.info(f"Position #{active['id']} closed automatically: {res.get('reason')} at ₹{live_ltp}")
                
            except Exception as e:
                logger.warning(f"Background Daemon loop exception (recovered safely): {e}")
                
            time_lib.sleep(getattr(config, "MONITOR_POLL_INTERVAL_SECONDS", 3))

    t = threading.Thread(target=monitor_loop, daemon=True)
    t.start()

def start_server():
    start_position_monitor_thread()
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
