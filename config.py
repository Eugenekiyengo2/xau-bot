"""Configuration. Reads from environment (.env locally, repo secrets in CI)."""
import os
from dotenv import load_dotenv

load_dotenv()


def _b(name, default=False):
    return os.getenv(name, str(default)).lower() in ("1", "true", "yes", "on")


class Config:
    # --- source ---
    SOURCE = os.getenv("SOURCE", "data")          # "data" (fetch candles) | "vision" (read screenshots)
    SYMBOL = os.getenv("SYMBOL", "XAU/USD")
    CHARTS_DIR = os.getenv("CHARTS_DIR", "charts")

    # --- indicator spec (Confluence Desk defaults) ---
    RSI_LEN = int(os.getenv("RSI_LEN", 9))
    MA_LEN = int(os.getenv("MA_LEN", 14))
    BB_MULT = float(os.getenv("BB_MULT", 2))
    SWING_LEN = int(os.getenv("SWING_LEN", 14))
    STOP_BUFFER_PCT = float(os.getenv("STOP_BUFFER_PCT", 0.0008))  # 0.08% beyond swept wick
    MIN_CONVICTION = int(os.getenv("MIN_CONVICTION", 25))

    # --- keys ---
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    VISION_MODEL = os.getenv("VISION_MODEL", "claude-sonnet-5-5")
    TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "")

    # --- macro ---
    NEWS_WINDOW_MIN = int(os.getenv("NEWS_WINDOW_MIN", 60))
    USE_COT = _b("USE_COT", True)
    USE_CALENDAR = _b("USE_CALENDAR", True)

    # --- alerts ---
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
    TWILIO_SID = os.getenv("TWILIO_SID", "")
    TWILIO_TOKEN = os.getenv("TWILIO_TOKEN", "")
    TWILIO_FROM = os.getenv("TWILIO_FROM", "")        # e.g. "whatsapp:+14155238886"
    WHATSAPP_TO = os.getenv("WHATSAPP_TO", "")        # e.g. "whatsapp:+2567..."

    # --- behaviour ---
    ALERT_ON = os.getenv("ALERT_ON", "actionable")    # "actionable" | "always"
    STATE_FILE = os.getenv("STATE_FILE", "last_state.json")


cfg = Config()
