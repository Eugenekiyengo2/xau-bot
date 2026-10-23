"""One analysis cycle. This is what the scheduler (cron / GitHub Actions) runs.

  python run_once.py

It reads the six timeframes (from live candles, or from chart screenshots if
SOURCE=vision), pulls COT + the news calendar, builds the signal, and alerts on a
change. Safe to run as often as every few minutes; it won't re-send the same signal."""
from __future__ import annotations
import json
import os
import sys

from config import cfg
from bot.models import MacroRead
from bot import engine, alerts


def gather_reads():
    reads = {}
    if cfg.SOURCE == "vision":
        from bot import vision
        reads = vision.read_all(cfg)
    else:
        from bot import data_price
        for tf in ("1W", "1D", "4H", "30m", "15m", "3m"):
            df = data_price.fetch_ohlcv(tf, cfg)
            if df is not None and len(df):
                reads[tf] = engine.read_from_df(tf, df, cfg)
    return reads


def gather_macro() -> MacroRead:
    m = MacroRead()
    if cfg.USE_COT:
        try:
            from bot import cot
            c = cot.fetch_cot(cfg)
            m.cot_stance = c["cot_stance"]
            m.cot_commercial_net = c["cot_commercial_net"]
            m.cot_mm_net = c["cot_mm_net"]
            m.cot_commercial_delta = c["cot_commercial_delta"]
            m.notes += c.get("notes", [])
        except Exception as e:  # noqa
            m.notes.append(f"COT error: {e}")
    if cfg.USE_CALENDAR:
        try:
            from bot import calendar_check
            cal = calendar_check.check_calendar(cfg)
            m.news_red_near = cal["news_red_near"]
            m.next_event = cal["next_event"]
            m.notes += cal.get("notes", [])
        except Exception as e:  # noqa
            m.notes.append(f"Calendar error: {e}")
    # USD stance is left neutral by default; wire a DXY/EURUSD read here if you want it.
    return m


def should_alert(sig) -> bool:
    if cfg.ALERT_ON == "always":
        return True
    key = f"{sig.bias}|{sig.posture}|{sig.entry}"
    prev = None
    if os.path.exists(cfg.STATE_FILE):
        try:
            prev = json.load(open(cfg.STATE_FILE)).get("key")
        except Exception:  # noqa
            prev = None
    json.dump({"key": key}, open(cfg.STATE_FILE, "w"))
    actionable = sig.bias in ("LONG", "SHORT")
    return actionable and key != prev


def main():
    reads = gather_reads()
    if not reads:
        print("No timeframe readings — check SOURCE / API key / chart images.")
        sys.exit(1)
    macro = gather_macro()
    sig = engine.build_signal(reads, macro, cfg)
    text = alerts.format_signal(sig, macro)
    print(text)
    print("-" * 40)
    if should_alert(sig):
        alerts.send_telegram(text, cfg)
        alerts.send_whatsapp(text, cfg)
        print("[alert] sent")
    else:
        print("[alert] no change / not actionable — nothing sent")


if __name__ == "__main__":
    main()
