"""
Keep-Alive Monitor for Render Cloud Instance.
Pings Iron Bot status endpoint every 4 minutes during market hours (09:15 to 15:30 IST)
to prevent Render Free Tier from falling asleep (eliminates cold-start webhook drop).
"""

import time
import ssl
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
URL = "https://iron-bot-yfo6.onrender.com/status"

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE

def ping():
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": "IronBot-KeepAlive/1.0"})
        with urllib.request.urlopen(req, context=_ctx, timeout=15) as resp:
            now_str = datetime.now(IST).strftime("%H:%M:%S")
            print(f"[{now_str} IST] Keep-Alive Ping 200 OK — Render is AWAKE & HOT", flush=True)
            return True
    except Exception as e:
        print(f"[Keep-Alive Err] {e}", flush=True)
        return False

if __name__ == "__main__":
    print("🚀 Starting Render Keep-Alive Daemon for Market Hours...")
    while True:
        now = datetime.now(IST)
        # Keep awake between 09:00 AM and 03:45 PM IST
        if 9 <= now.hour <= 15:
            ping()
        else:
            print(f"[{now.strftime('%H:%M:%S')} IST] Market closed, skipping ping.", flush=True)
        time.sleep(240) # Every 4 minutes (before 15-min timeout)
