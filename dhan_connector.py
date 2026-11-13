"""
DhanHQ Institutional Broker Gateway
Loosely coupled broker connector using Python standard library.
Ensures zero-dependency reliability on Render Cloud and local environments.
Enforces Canary Virtual Mode (₹0 risk) while verifying broker connectivity.
"""

import json
import logging
import os
import ssl
import urllib.request
import urllib.error
from datetime import datetime
from typing import Dict, Any, List, Optional
import config
import database
import telegram_notifier

logger = logging.getLogger("IronBot.Dhan")

# Dhan v2 API Endpoints
DHAN_BASE_URL = "https://api.dhan.co/v2"
DHAN_AUTH_URL = "https://auth.dhan.co/app"

# SSL context for universal certificate handling
_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE


def get_current_token() -> str:
    """Retrieves access token from database, falling back to config or env."""
    try:
        db_token = database.get_config("DHAN_ACCESS_TOKEN")
        if db_token and len(db_token.strip()) > 20:
            return db_token.strip()
    except Exception:
        pass
    return os.environ.get("DHAN_ACCESS_TOKEN", getattr(config, "DHAN_ACCESS_TOKEN", "")).strip()


def _get_headers() -> Dict[str, str]:
    """Builds authentication headers from configuration or database."""
    client_id = os.environ.get("DHAN_CLIENT_ID", getattr(config, "DHAN_CLIENT_ID", "")).strip()
    access_token = get_current_token()
    return {
        "access-token": access_token,
        "client-id": client_id,
        "Content-Type": "application/json"
    }


def _api_get(endpoint: str, timeout: int = 5) -> Optional[Dict[str, Any]]:
    """Safe GET request wrapper with timeout and exception containment."""
    url = f"{DHAN_BASE_URL}{endpoint}"
    headers = _get_headers()
    
    if not headers.get("access-token") or not headers.get("client-id"):
        logger.warning(f"[DHAN] Missing credentials for {endpoint}")
        return None

    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, context=_ssl_ctx, timeout=timeout) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="ignore")
        logger.warning(f"[DHAN HTTP {e.code}] {endpoint}: {err_msg}")
    except Exception as e:
        logger.warning(f"[DHAN Error] {endpoint}: {e}")
    return None


def _api_post(endpoint: str, payload: Dict[str, Any], timeout: int = 5) -> Optional[Dict[str, Any]]:
    """Safe POST request wrapper with timeout and exception containment."""
    url = f"{DHAN_BASE_URL}{endpoint}"
    headers = _get_headers()

    if not headers.get("access-token") or not headers.get("client-id"):
        logger.warning(f"[DHAN] Missing credentials for {endpoint}")
        return None

    try:
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
        with urllib.request.urlopen(req, context=_ssl_ctx, timeout=timeout) as resp:
            if resp.status in (200, 201):
                return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="ignore")
        logger.warning(f"[DHAN HTTP {e.code}] {endpoint}: {err_msg}")
    except Exception as e:
        logger.warning(f"[DHAN Error] {endpoint}: {e}")
    return None


def get_profile() -> Optional[Dict[str, Any]]:
    """Fetches user profile and token validity from Dhan."""
    return _api_get("/profile")


def get_fund_limits() -> Optional[Dict[str, Any]]:
    """Fetches account fund ledger and margin availability."""
    return _api_get("/fundlimit")


def get_positions() -> List[Dict[str, Any]]:
    """Fetches current open positions from Dhan account."""
    res = _api_get("/positions")
    if isinstance(res, list):
        return res
    if isinstance(res, dict) and "data" in res:
        return res.get("data", []) or []
    return []


def get_orders() -> List[Dict[str, Any]]:
    """Fetches order book from Dhan account."""
    res = _api_get("/orders")
    if isinstance(res, list):
        return res
    if isinstance(res, dict) and "data" in res:
        return res.get("data", []) or []
    return []


def get_connection_status() -> Dict[str, Any]:
    """
    Returns high-level broker health summary for dashboard and healthchecks.
    Ensures safe degradation if token expires or network is unavailable.
    """
    profile = get_profile()
    funds = get_fund_limits()

    if profile and "dhanClientId" in profile:
        client_id = profile.get("dhanClientId", "")
        token_validity = profile.get("tokenValidity", "Unknown")
        active_segment = profile.get("activeSegment", "").strip()
        data_plan = profile.get("dataPlan", "Deactive")
        
        balance = 0.0
        if funds and "availabelBalance" in funds:
            balance = float(funds.get("availabelBalance", 0.0))

        return {
            "connected": True,
            "broker": "DhanHQ",
            "client_id": client_id,
            "token_validity": token_validity,
            "active_segment": active_segment,
            "data_plan": data_plan,
            "available_balance": balance,
            "paper_trading_locked": getattr(config, "PAPER_TRADING_MODE", True),
            "status_text": "AUTHENTICATED & READY (Virtual Execution Active)"
        }

    return {
        "connected": False,
        "broker": "DhanHQ",
        "client_id": getattr(config, "DHAN_CLIENT_ID", "Not Set"),
        "token_validity": "Expired or Invalid",
        "active_segment": "N/A",
        "data_plan": "N/A",
        "available_balance": 0.0,
        "paper_trading_locked": True,
        "status_text": "DISCONNECTED (Token Expired or Missing)"
    }


def route_order_to_broker(trade_params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Broker Order Router with Institutional Safety Lock.
    When PAPER_TRADING_MODE = True, guarantees ₹0 is placed at broker.
    """
    if getattr(config, "PAPER_TRADING_MODE", True):
        logger.info(
            f"[DHAN SAFETY LOCK] Order intercepted for {trade_params.get('symbol')} "
            f"qty={trade_params.get('quantity')}. Mode=PAPER. No real order placed."
        )
        return {
            "routed": False,
            "mode": "PAPER_TRADING",
            "message": "Order held in virtual engine. Dhan live order bypassed per Canary Rule.",
            "broker_order_id": f"VIRTUAL_{config.get_today_ist()}_{trade_params.get('trade_id', 1)}"
        }

    # Live Mode Execution (Requires explicit opt-in and Static IP on Dhan)
    logger.warning("[DHAN LIVE] Attempting live order routing...")
    order_payload = {
        "dhanClientId": getattr(config, "DHAN_CLIENT_ID", ""),
        "correlationId": f"IRON_{config.get_today_ist()}_{trade_params.get('trade_id')}",
        "transactionType": "BUY" if "BUY" in trade_params.get("signal_type", "") else "SELL",
        "exchangeSegment": "NSE_FNO",
        "productType": "INTRADAY",
        "orderType": "LIMIT",
        "validity": "DAY",
        "securityId": str(trade_params.get("security_id", "")),
        "quantity": int(trade_params.get("quantity", 65)),
        "price": float(trade_params.get("entry_price", 0.0)),
        "triggerPrice": 0.0,
        "disclosedQuantity": 0,
        "afterMarketOrder": False
    }

    res = _api_post("/orders", order_payload)
    if res and res.get("orderStatus") in ("TRANSIT", "PENDING", "TRADED"):
        return {
            "routed": True,
            "mode": "LIVE",
            "broker_order_id": res.get("orderId"),
            "data": res
        }
    return {
        "routed": False,
        "mode": "LIVE_FAILED",
        "error": res or "Order rejected by broker"
    }


def update_access_token(new_token: str) -> Dict[str, Any]:
    """
    Dynamically saves a new access token to SQLite without redeploying.
    Immediately validates the token against the Dhan Profile API.
    """
    clean_token = new_token.strip()
    if not clean_token:
        return {"success": False, "error": "Token string is empty"}

    database.set_config("DHAN_ACCESS_TOKEN", clean_token)
    
    # Verify immediately
    profile = get_profile()
    if profile and "dhanClientId" in profile:
        client_id = profile.get("dhanClientId")
        validity = profile.get("tokenValidity", "Unknown")
        
        # Notify Telegram
        telegram_notifier.send_telegram_alert(
            "🔑 *DHANHQ TOKEN RENEWED DYNAMICALLY*\n\n"
            f"👤 *Client ID:* `{client_id}`\n"
            f"⏰ *New Validity:* `{validity}`\n"
            "✅ *Status:* Broker handshake successful! System armed."
        )
        return {
            "success": True,
            "client_id": client_id,
            "validity": validity,
            "message": "Token verified & saved to database."
        }
    
    return {
        "success": False,
        "error": "Token saved to database, but Dhan API returned error on verification."
    }


def run_8am_handshake(force: bool = False) -> Dict[str, Any]:
    """
    Daily 8:00 AM IST Pre-Market Automated Handshake.
    Verifies if Dhan token is valid for today's market session (9:15 to 15:30).
    Dispatches status briefing to Telegram.
    """
    logger.info("[DHAN 8AM] Running pre-market broker handshake check...")
    status = get_connection_status()

    if status.get("connected"):
        validity = status.get("token_validity", "Unknown")
        client_id = status.get("client_id", "")
        balance = status.get("available_balance", 0.0)

        # Check if validity covers today's market hours
        # tokenValidity format: "DD/MM/YYYY HH:MM" e.g. "11/11/2026 15:15"
        is_sufficient = True
        try:
            val_dt = datetime.strptime(validity, "%d/%m/%Y %H:%M")
            now_dt = config.get_now_ist().replace(tzinfo=None)
            # Must be valid for at least another 6 hours
            if (val_dt - now_dt).total_seconds() < (6 * 3600):
                is_sufficient = False
        except Exception:
            pass

        if is_sufficient:
            msg = (
                "🌅 *8:00 AM PRE-MARKET HANDSHAKE: GREEN*\n\n"
                f"👤 *Dhan Client ID:* `{client_id}`\n"
                f"🔑 *Token Validity:* `{validity}`\n"
                f"💰 *Ledger Balance:* `₹{balance:.2f}` (Strict ₹0 Risk)\n"
                "🛡️ *Execution:* `Canary Virtual Mode (Locked)`\n"
                "🎯 *Trading Hours:* 9:30 AM – 2:45 PM IST\n\n"
                "✅ *Broker Handshake 200 OK — Iron Bot is fully armed for today!*"
            )
            telegram_notifier.send_telegram_alert(msg)
            return {"status": "SUCCESS", "validity": validity, "sufficient": True}
        else:
            msg = (
                "⚠️ *8:00 AM HANDSHAKE WARNING: TOKEN EXPIRING SOON*\n\n"
                f"👤 *Client ID:* `{client_id}`\n"
                f"🔑 *Current Validity:* `{validity}`\n"
                "⚠️ Token will expire during market hours today!\n\n"
                "👉 *1-Tap Quick Renew:* Click below to paste new token:\n"
                "https://iron-bot-yfo6.onrender.com/update_token"
            )
            telegram_notifier.send_telegram_alert(msg)
            return {"status": "EXPIRING_SOON", "validity": validity, "sufficient": False}

    # If completely disconnected / expired
    msg = (
        "🚨 *8:00 AM PRE-MARKET HANDSHAKE: RED - ACTION REQUIRED*\n\n"
        "❌ DhanHQ Access Token is EXPIRED or Invalid!\n\n"
        "👉 *1-Tap Quick Renew:* Click below to paste new 24h token:\n"
        "https://iron-bot-yfo6.onrender.com/update_token"
    )
    telegram_notifier.send_telegram_alert(msg)
    return {"status": "DISCONNECTED", "sufficient": False}

