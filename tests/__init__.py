"""
Unit Test Package Initialization for Iron Bot
Enforces:
1. Strict test isolation using an ephemeral test SQLite database.
   Prevents test execution from colliding with the active EC2 production daemon (iron-bot.service).
2. Sets IRON_BOT_TEST_MODE=1 to bypass live Telegram network calls during rapid testing.
"""

import os
import tempfile

os.environ["IRON_BOT_TEST_MODE"] = "1"
if "IRON_BOT_DB_PATH" not in os.environ:
    os.environ["IRON_BOT_DB_PATH"] = os.path.join(tempfile.gettempdir(), "iron_bot_unit_tests.db")

import database
database.init_db()
