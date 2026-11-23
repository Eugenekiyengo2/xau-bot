"""One analysis cycle. This is what the scheduler (cron / GitHub Actions) runs.

  python run_once.py

It reads the six timeframes (from live candles, or from chart screenshots if
SOURCE=vision), pulls COT + the news calendar, builds the signal, and alerts on a
change. Safe to run as often as every few minutes; it won't re-send the same signal."""
from __future__ import annotations
import json
import os
import sys
import time

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
    # The inputs that used to be declared and never computed.
    try:
        from bot import macro_extra
        m.usd_stance, m.usd_note = macro_extra.usd_stance(cfg)
        if m.usd_note:
            m.notes.append(m.usd_note)
    except Exception as e:  # noqa
        m.notes.append(f"USD read failed: {e}")
    try:
        from bot import macro_extra
        m.sentiment_stance, m.sentiment_note = macro_extra.sentiment_stance(cfg)
    except Exception:  # noqa
        pass
    return m


def slug(symbol: str) -> str:
    """A filename-safe tag, so each market keeps its own state and journal."""
    return symbol.replace("/", "").replace(" ", "").upper()


def should_alert(sig, symbol: str) -> bool:
    """One liquidity grab should produce ONE alert.

    The setup stays valid for many bars while price hovers near the swept level, so
    keying on the entry price — which moves every bar — fired the same trade over and
    over. Keying on the swept level instead means the alert repeats only when a genuinely
    new level has been taken.
    """
    if cfg.ALERT_ON == "always":
        return True
    path = f"{slug(symbol)}_{cfg.STATE_FILE}"
    setup = round(sig.sweep_level, 2) if sig.sweep_level is not None else sig.entry
    key = f"{sig.bias}|{setup}"
    prev = None
    if os.path.exists(path):
        try:
            prev = json.load(open(path)).get("key")
        except Exception:  # noqa
            prev = None
    json.dump({"key": key}, open(path, "w"))
    if sig.bias not in ("LONG", "SHORT"):
        return False
    if key == prev:
        print(f"[alert] same setup as last time (level {setup}) — not repeating")
        return False
    return True


def update_journal(sig, frames, symbol: str):
    """Record this signal and settle any earlier one against the price since."""
    if not getattr(cfg, "USE_JOURNAL", True):
        return
    try:
        from bot import journal
        path = f"{slug(symbol)}_{cfg.JOURNAL_FILE}"
        df = journal.load(path)

        # resolve open entries using the finest timeframe we have candles for
        candles = next((frames[tf] for tf in ("3m", "15m", "30m", "4H") if tf in frames), None)
        df, closed = journal.resolve(df, candles)
        for c in closed:
            print(f"[journal] {c['id']} closed: {c['status']} ({c['result_r']:+.2f}R)")

        df, added = journal.append_signal(df, sig)
        if added:
            print(f"[journal] recorded new {sig.bias} signal")
        journal.save(df, path)
        print("[journal] " + journal.summary(df).replace("\n", "\n[journal] "))
    except Exception as e:  # noqa - the journal must never break the alert
        print(f"[journal] skipped: {e}")


def why_not(sig) -> str:
    """The one sentence that explains why this market is not trading right now."""
    markers = ("Blocked:", "Outside the", "Rejected:", "below the", "No entry trigger",
               "waiting for confirmation", "Rotation", "Red-news")
    for r in sig.reasons[1:]:
        if any(m in r for m in markers):
            text = r.split(" — ")[0].split(". ")[0]
            return text[:90]
    return "no trigger"


def analyse(symbol: str, macro) -> dict | None:
    """One market, end to end. Returns a short result for the status digest."""
    cfg.SYMBOL = symbol                       # the data layer reads this
    print("\n" + "=" * 52)
    print(f"  {symbol}")
    print("=" * 52)

    reads, frames = gather_reads()
    if not reads:
        print(f"No readings for {symbol} — skipped.")
        return None

    # These two are market-specific, so they are computed per symbol rather than once.
    try:
        from bot import macro_extra, sessions
        macro.vol_regime, macro.vol_note = macro_extra.vol_regime(frames)
        intra = next((frames[t] for t in ("15m", "30m", "4H") if t in frames), None)
        if intra is not None and len(intra):
            macro.session_bias, macro.session_note = sessions.cascade_bias(
                intra, intra["datetime"].iloc[-1])
    except Exception as e:  # noqa
        print(f"[macro] session/volatility read skipped: {e}")

    sig = engine.build_signal(reads, macro, cfg)
    text = f"[{symbol}]\n" + alerts.format_signal(sig, macro, cfg, symbol)
    print(text)
    update_journal(sig, frames, symbol)
    if should_alert(sig, symbol):
        alerts.send_telegram(text, cfg)
        alerts.send_whatsapp(text, cfg)
        print("[alert] sent")
    else:
        print("[alert] no change / not actionable — nothing sent")
    return {"symbol": symbol, "bias": sig.bias, "posture": sig.posture,
            "conviction": sig.conviction, "why": why_not(sig),
            "score": sig.reasons[0] if sig.reasons else ""}


def status_digest(results: list) -> str:
    """A short note proving the bot is alive and saying what is holding it back."""
    from datetime import datetime, timezone
    lines = [f"📊 XAU bot — status {datetime.now(timezone.utc):%H:%M} UTC",
             f"Checked {len(results)} markets, no trade taken.", ""]
    for r in results:
        tag = {"ACCUMULATION": "↑", "DISTRIBUTION": "↓"}.get(r["posture"], "·")
        lines.append(f"{tag} {r['symbol']:<9} conv {r['conviction']:>3}  {r['why']}")
    lines += ["", "Silence means the filters are holding, not that the bot is down."]
    return "\n".join(lines)


def maybe_send_status(results: list):
    """Send the digest at most every STATUS_EVERY_MIN minutes, so it informs
    rather than floods: 240 evaluations a day would be 240 notifications."""
    every = int(getattr(cfg, "STATUS_EVERY_MIN", 0) or 0)
    if every <= 0 or not results:
        return
    path = "status_last.json"
    now = time.time()
    try:
        last = json.load(open(path)).get("at", 0) if os.path.exists(path) else 0
    except Exception:  # noqa
        last = 0
    if now - last < every * 60:
        mins = (every * 60 - (now - last)) / 60
        print(f"[status] next digest in {mins:.0f} min")
        return
    text = status_digest(results)
    print(text)
    if alerts.send_telegram(text, cfg):
        print("[status] digest sent")
    try:
        json.dump({"at": now}, open(path, "w"))
    except Exception:  # noqa
        pass


def main():
    symbols = [s.strip() for s in getattr(cfg, "SYMBOLS", cfg.SYMBOL).split(",") if s.strip()]

    # Each market costs one API call per timeframe. On a free plan that budget is the
    # binding constraint, so say plainly what this run will consume.
    per_run = len(symbols) * 6
    print(f"Markets: {', '.join(symbols)}   (~{per_run} API calls this run)")

    macro = gather_macro()                    # COT and the calendar are gold-specific
    results = []
    for sym in symbols:
        try:
            r = analyse(sym, macro)
            if r:
                results.append(r)
        except Exception as e:  # noqa - one bad market must not stop the rest
            print(f"[{sym}] failed: {e}")
    if not results:
        print("\nNo market produced a reading — check SOURCE / API key / symbols.")
        sys.exit(1)

    # Only digest when nothing actually fired; a real signal speaks for itself.
    if not any(r["bias"] in ("LONG", "SHORT") for r in results):
        maybe_send_status(results)
    print(f"\nDone: {len(results)}/{len(symbols)} markets analysed.")


if __name__ == "__main__":
    main()
