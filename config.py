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
    # Markets to scan. Each one costs ~6 API calls per run, so on a free
    # plan (800/day) keep the list short or slow the schedule down.
    SYMBOLS = os.getenv("SYMBOLS", "XAU/USD")
    # Reuse recently downloaded candles instead of refetching every run.
    USE_TF_CACHE = _b("USE_TF_CACHE", True)
    CHARTS_DIR = os.getenv("CHARTS_DIR", "charts")

    # --- indicator spec (Confluence Desk defaults) ---
    RSI_LEN = int(os.getenv("RSI_LEN", 9))
    MA_LEN = int(os.getenv("MA_LEN", 14))
    BB_MULT = float(os.getenv("BB_MULT", 2))
    SWING_LEN = int(os.getenv("SWING_LEN", 14))
    SWEEP_LOOKBACK = int(os.getenv("SWEEP_LOOKBACK", 30))     # bars to scan for a grab
    # London open and New York open, in UTC hours. Empty disables the filter.
    KILLZONES = [tuple(int(x) for x in z.split("-"))
                 for z in os.getenv("KILLZONES", "7-11,12-16").split(",") if z.strip()]
    REQUIRE_MSS = _b("REQUIRE_MSS", True)        # a grab must be confirmed by a structure shift
    PRECISION_ENTRY = _b("PRECISION_ENTRY", True)  # enter in the imbalance, not at market
    SWEEP_MAX_DRIFT_ATR = float(os.getenv("SWEEP_MAX_DRIFT_ATR", 6.0))  # ...but only while price is still near it
    STOP_BUFFER_PCT = float(os.getenv("STOP_BUFFER_PCT", 0.0008))  # 0.08% beyond swept wick
    # How much each input argues for a direction. Tune these, not the code.
    W_COT = float(os.getenv("W_COT", 2.0))            # bank/commercial positioning
    W_USD = float(os.getenv("W_USD", 1.5))            # dollar direction
    W_SENTIMENT = float(os.getenv("W_SENTIMENT", 1.0))  # retail crowd, faded
    W_SESSION = float(os.getenv("W_SESSION", 1.5))    # Asia -> London -> NY cascade
    W_MSS = float(os.getenv("W_MSS", 2.0))            # confirmed structure shift
    RETAIL_LONG_PCT = os.getenv("RETAIL_LONG_PCT", "")  # set from Myfxbook if you track it
    # Your account, and how much of it you choose to put at risk on one trade.
    # RISK_PCT is YOUR decision. sizing.py will tell you what the journal supports;
    # this is what the bot will actually size to.
    ACCOUNT_BALANCE = float(os.getenv("ACCOUNT_BALANCE", 1000))
    RISK_PCT = float(os.getenv("RISK_PCT", 0.005))
    LEVERAGE = int(os.getenv("LEVERAGE", 500))
    MIN_CONVICTION = int(os.getenv("MIN_CONVICTION", 25))
    MAX_RISK_ATR = float(os.getenv("MAX_RISK_ATR", 3.0))   # reject a setup whose stop is this wide
    MIN_STOP_ATR = float(os.getenv("MIN_STOP_ATR", 0.5))   # stop never tighter than 0.5x ATR
    MAX_TP_R = float(os.getenv("MAX_TP_R", 6.0))           # TP2 never further than 6R
    TP2_MIN_R = float(os.getenv("TP2_MIN_R", 1.5))         # ...nor nearer than 1.5R
    TP_COUNT = int(os.getenv("TP_COUNT", 3))               # how many take-profit levels
    TREND_MA = int(os.getenv("TREND_MA", 200))          # long MA defining the primary trend
    USE_TREND_FILTER = _b("USE_TREND_FILTER", True)     # refuse trades that fight it
    # which timeframes may fire an entry; the rest only set posture
    EXEC_TFS = tuple(t.strip() for t in os.getenv("EXEC_TFS", "3m,15m").split(",") if t.strip())

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
    JOURNAL_FILE = os.getenv("JOURNAL_FILE", "journal.csv")
    USE_JOURNAL = _b("USE_JOURNAL", True)


cfg = Config()
