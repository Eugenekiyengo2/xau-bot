"""Forward-test journal: a permanent, hindsight-free record of every live signal.

Each actionable signal is written to a CSV when it fires. On later runs, any still-open
entry is checked against the price action that has happened since, and marked as a stop
or a target hit. Nothing here is retrospective — a row is written before the outcome is
known, which is exactly what makes the record trustworthy, and what a backtest can never
be.

After a few months, `python review.py` turns this into the only performance figure that
really counts: how the strategy did on live signals, in real time, with no hindsight.
"""
from __future__ import annotations
import os
from datetime import datetime, timezone

import pandas as pd

COLUMNS = ["id", "opened_utc", "dir", "entry", "stop", "tp1", "tp2", "tp3", "risk",
           "conviction", "posture", "status", "hit", "closed_utc", "bars", "result_r", "best_r"]
OPEN = "open"


def load(path: str) -> pd.DataFrame:
    if os.path.exists(path):
        try:
            df = pd.read_csv(path)
            for c in COLUMNS:
                if c not in df.columns:
                    df[c] = None
            return df[COLUMNS]
        except Exception as e:  # noqa
            print(f"[journal] could not read {path}: {e}")
    return pd.DataFrame(columns=COLUMNS)


def save(df: pd.DataFrame, path: str):
    df.to_csv(path, index=False)


def append_signal(df: pd.DataFrame, sig, now: datetime | None = None) -> tuple[pd.DataFrame, bool]:
    """Record a new LONG/SHORT. Returns (frame, added?). Duplicates are not re-recorded."""
    if sig.bias not in ("LONG", "SHORT") or sig.entry is None or not sig.tps:
        return df, False
    now = now or datetime.now(timezone.utc)
    sig_id = f"{now:%Y%m%dT%H%M}-{sig.bias}-{sig.entry}"
    if not df.empty and (df["id"].astype(str) == sig_id).any():
        return df, False
    # don't stack a second entry while one is still running
    if not df.empty and (df["status"].astype(str) == OPEN).any():
        return df, False

    row = {
        "id": sig_id, "opened_utc": now.isoformat(timespec="seconds"), "dir": sig.bias,
        "entry": sig.entry, "stop": sig.stop, "tp1": sig.tps[0], "tp2": sig.tps[1],
        "tp3": sig.tps[2], "risk": sig.risk, "conviction": sig.conviction,
        "posture": sig.posture, "status": OPEN, "hit": "", "closed_utc": "",
        "bars": 0, "result_r": "", "best_r": 0.0,
    }
    return pd.concat([df, pd.DataFrame([row])], ignore_index=True), True


def resolve(df: pd.DataFrame, candles: pd.DataFrame, cost_r: float = 0.05) -> tuple[pd.DataFrame, list]:
    """Walk open entries forward through candles that printed after they opened."""
    if df.empty or candles is None or candles.empty:
        return df, []
    closed = []
    for i, row in df.iterrows():
        if str(row.get("status")) != OPEN:
            continue
        try:
            opened = pd.Timestamp(row["opened_utc"]).tz_localize(None)
        except Exception:  # noqa
            continue
        later = candles[candles["datetime"] > opened]
        if later.empty:
            continue

        long = row["dir"] == "LONG"
        entry, stop, risk = float(row["entry"]), float(row["stop"]), float(row["risk"])
        tps = [float(row["tp1"]), float(row["tp2"]), float(row["tp3"])]
        hit = [int(h) for h in str(row.get("hit") or "").split("|") if h.strip().isdigit()]
        best = float(row.get("best_r") or 0.0)
        status, closed_at = OPEN, None

        for _, bar in later.iterrows():
            hi, lo = float(bar["high"]), float(bar["low"])
            fav = (hi - entry) if long else (entry - lo)
            best = max(best, fav / risk if risk else 0.0)
            for n, tp in enumerate(tps, start=1):
                if (hi >= tp if long else lo <= tp) and n not in hit:
                    hit.append(n)
            stopped = lo <= stop if long else hi >= stop
            if stopped:                      # conservative: stop wins a tied bar
                status, closed_at = "stop", bar["datetime"]
                break
            if 3 in hit:
                status, closed_at = "tp3", bar["datetime"]
                break

        df.at[i, "hit"] = "|".join(str(h) for h in sorted(hit))
        df.at[i, "best_r"] = round(best, 2)
        df.at[i, "bars"] = int(row.get("bars") or 0) + len(later)
        if status != OPEN:
            df.at[i, "status"] = status
            df.at[i, "closed_utc"] = str(closed_at)
            df.at[i, "result_r"] = round(_scaled_r(hit, tps, entry, risk, cost_r), 2)
            closed.append({"id": row["id"], "dir": row["dir"], "status": status,
                           "result_r": df.at[i, "result_r"]})
    return df, closed


def _scaled_r(hit: list, tps: list, entry: float, risk: float, cost_r: float) -> float:
    """50% banked at TP1 (rest to breakeven), 30% at TP2, 20% at TP3."""
    if not hit:
        return -1.0 - cost_r
    r = 0.5 * (abs(tps[0] - entry) / risk)
    if 2 in hit:
        r += 0.3 * (abs(tps[1] - entry) / risk)
    if 3 in hit:
        r += 0.2 * (abs(tps[2] - entry) / risk)
    return r - cost_r


def summary(df: pd.DataFrame) -> str:
    if df.empty:
        return "No signals recorded yet."
    done = df[df["status"].astype(str) != OPEN]
    openn = len(df) - len(done)
    out = [f"Signals recorded: {len(df)}   resolved: {len(done)}   open: {openn}"]
    if done.empty:
        return "\n".join(out) + "\nNothing has resolved yet — nothing can be concluded."
    rs = pd.to_numeric(done["result_r"], errors="coerce").dropna()
    if rs.empty:
        return "\n".join(out)
    wins = (rs > 0).sum()
    out.append(f"Win rate: {100*wins/len(rs):.1f}%   total {rs.sum():+.2f}R   "
               f"average {rs.mean():+.2f}R")
    if len(rs) >= 2:
        se = rs.std(ddof=1) / (len(rs) ** 0.5)
        out.append(f"95% confidence range for average R: "
                   f"{rs.mean()-2*se:+.2f} to {rs.mean()+2*se:+.2f}")
    if len(rs) < 30:
        out.append(f"NOT YET MEANINGFUL — {len(rs)} resolved trades. Needs 30+.")
    return "\n".join(out)
