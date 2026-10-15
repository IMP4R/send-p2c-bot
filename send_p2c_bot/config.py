import os
from dotenv import load_dotenv

load_dotenv()

# === Telegram Bot ===
BOT_TOKEN = os.getenv("BOT_TOKEN", '')
ADMIN_IDS = [
    int(x) for x in os.getenv("ADMIN_IDS", '0').split(",") if x.strip()
]

# === Пути ===
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "bot.db")
COOKIES_DIR = os.path.join(DATA_DIR, "cookies")
QR_DIR = os.path.join(DATA_DIR, "qr_codes")

for d in [DATA_DIR, COOKIES_DIR, QR_DIR]:
    os.makedirs(d, exist_ok=True)

# === P2C ===
QR_REFRESH_INTERVAL = int(os.getenv("QR_REFRESH_INTERVAL", "300"))
P2C_CHECK_INTERVAL = int(os.getenv("P2C_CHECK_INTERVAL", "10"))

# === Send.TG API ===
SEND_TG_BASE_URL = os.getenv("SEND_TG_BASE_URL", "https://app.send.tg")
SEND_TG_API_URL = f"{SEND_TG_BASE_URL}/internal/v1"

USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/127.0.0.1 Safari/537.36",
)