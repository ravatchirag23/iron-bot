"""
Iron Bot Webhook Gateway & Live Dashboard Server
Zero-dependency HTTP server running on Python standard library.
Handles Webhooks from GoCharting, validates 13 Iron Armor Rules, and executes via Paper Engine.
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
import json
import logging
from datetime import datetime
import os
import config
import database
import risk_manager
import paper_engine
import telegram_notifier

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

    def do_GET(self):
        if self.path == "/" or self.path == "/status":
            self.handle_status()
        elif self.path == "/trades":
            trades = database.get_all_trades(50)
            self._send_json(200, {"trades": trades})
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
        trade_id = paper_engine.engine.process_entry(trade_params)
        
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
        today_str = datetime.now().strftime("%Y-%m-%d")
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

def start_server():
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
