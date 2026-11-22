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
    """Returns (readings, raw candles). The candles are kept so the journal can
    resolve earlier signals against the price action that has printed since."""
    reads, frames = {}, {}
    if cfg.SOURCE == "vision":
        from bot import vision
        reads = vision.read_all(cfg)
    else:
        from bot import data_price
        for tf in ("1W", "1D", "4H", "30m", "15m", "3m"):
            df = data_price.fetch_ohlcv(tf, cfg)
            if df is not None and len(df):
                frames[tf] = df
                reads[tf] = engine.read_from_df(tf, df, cfg)
    return reads, frames


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


def update_journal(sig, frames):
    """Record this signal and settle any earlier one against the price since."""
    if not getattr(cfg, "USE_JOURNAL", True):
        return
    try:
        from bot import journal
        df = journal.load(cfg.JOURNAL_FILE)

        # resolve open entries using the finest timeframe we have candles for
        candles = next((frames[tf] for tf in ("3m", "15m", "30m", "4H") if tf in frames), None)
        df, closed = journal.resolve(df, candles)
        for c in closed:
            print(f"[journal] {c['id']} closed: {c['status']} ({c['result_r']:+.2f}R)")

        df, added = journal.append_signal(df, sig)
        if added:
            print(f"[journal] recorded new {sig.bias} signal")
        journal.save(df, cfg.JOURNAL_FILE)
        print("[journal] " + journal.summary(df).replace("\n", "\n[journal] "))
    except Exception as e:  # noqa - the journal must never break the alert
        print(f"[journal] skipped: {e}")


def main():
    reads, frames = gather_reads()
    if not reads:
        print("No timeframe readings — check SOURCE / API key / chart images.")
        sys.exit(1)
    macro = gather_macro()
    sig = engine.build_signal(reads, macro, cfg)
    text = alerts.format_signal(sig, macro)
    print(text)
    print("-" * 40)
    update_journal(sig, frames)
    if should_alert(sig):
        alerts.send_telegram(text, cfg)
        alerts.send_whatsapp(text, cfg)
        print("[alert] sent")
    else:
        print("[alert] no change / not actionable — nothing sent")


if __name__ == "__main__":
    main()
