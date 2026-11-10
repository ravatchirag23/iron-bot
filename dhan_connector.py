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
from typing import Dict, Any, List, Optional
import config

logger = logging.getLogger("IronBot.Dhan")

# Dhan v2 API Endpoints
DHAN_BASE_URL = "https://api.dhan.co/v2"

# SSL context for universal certificate handling
_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE


def _get_headers() -> Dict[str, str]:
    """Builds authentication headers from configuration or environment variables."""
    client_id = os.environ.get("DHAN_CLIENT_ID", getattr(config, "DHAN_CLIENT_ID", ""))
    access_token = os.environ.get("DHAN_ACCESS_TOKEN", getattr(config, "DHAN_ACCESS_TOKEN", ""))
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
