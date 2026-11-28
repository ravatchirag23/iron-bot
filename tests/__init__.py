"""
Unit Test Package Initialization for Iron Bot
Enforces:
1. Strict test isolation using a fresh ephemeral test SQLite database.
   Prevents test execution from colliding with the active EC2 production daemon (iron-bot.service).
2. Sets IRON_BOT_TEST_MODE=1 to bypass live Telegram network calls during rapid testing.
3. Automatically starts with a pristine database on each test suite run.
"""

import os
import tempfile

os.environ["IRON_BOT_TEST_MODE"] = "1"
if "IRON_BOT_DB_PATH" not in os.environ:
    os.environ["IRON_BOT_DB_PATH"] = os.path.join(tempfile.gettempdir(), "iron_bot_unit_tests.db")

test_db = os.environ["IRON_BOT_DB_PATH"]
if os.path.exists(test_db):
    try:
        os.remove(test_db)
    except Exception:
        pass

import database
database.init_db()
database.init_campaign_wallet(100000.0)
