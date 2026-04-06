import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
TATUM_API_KEY = os.getenv("TATUM_API_KEY")

# LTC addresses (should be 5 addresses comma separated)
LTC_ADDRESSES = [addr.strip() for addr in os.getenv("LTC_ADDRESSES", "").split(",") if addr.strip()]

# Deposit timeout in minutes
DEPOSIT_TIMEOUT_MINUTES = 30

# SQLite / Postgres Database path
DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("NEON_DB")
DB_PATH = os.path.abspath(os.getenv("DB_PATH", "bot_database.sqlite"))

# Assets directory for persistence
ASSETS_DIR = os.getenv("ASSETS_DIR", "assets")
