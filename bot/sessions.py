"""Session cascade: Asia sets the liquidity, London hunts it, New York hunts London's.

The idea the ICT material teaches is that the Asian session builds a tight range while
the big desks are quiet, and that range's high and low become the obvious pools of
stops. London then takes one side of it — often as a false move, the "judas swing" —
and the genuine direction of the day is whatever follows that grab. New York repeats
the pattern against London's own range.

So a sweep is read differently depending on which session is running and what the
previous session left behind. All times are UTC.
"""
from __future__ import annotations
import pandas as pd

ASIA = (0, 7)
LONDON = (7, 12)
NEWYORK = (12, 17)
NAMES = {"asia": ASIA, "london": LONDON, "newyork": NEWYORK}


def current_session(ts) -> str | None:
    try:
        h = pd.Timestamp(ts).hour
    except Exception:  # noqa
        return None
    for name, (a, b) in NAMES.items():
        if a <= h < b:
            return name
    return None


def session_range(df: pd.DataFrame, ts, window) -> tuple | None:
    """High and low of one session on the day that `ts` falls in."""
    if df is None or df.empty or "datetime" not in df.columns:
        return None
    day = pd.Timestamp(ts).normalize()
    lo_t = day + pd.Timedelta(hours=window[0])
    hi_t = day + pd.Timedelta(hours=window[1])
    seg = df[(df["datetime"] >= lo_t) & (df["datetime"] < hi_t)]
    if seg.empty:
        return None
    return float(seg["high"].max()), float(seg["low"].min())


def cascade_bias(df: pd.DataFrame, ts) -> tuple:
    """What the previous session's range says about this one.

    Returns (bias, explanation). Taking the low and reclaiming it is bullish — the
    stops below were collected and price rejected lower prices. Taking the high and
    failing is the mirror. Breaking a side and holding beyond it is continuation.
    """
    sess = current_session(ts)
    if sess == "london":
        ref, label = session_range(df, ts, ASIA), "Asian"
    elif sess == "newyork":
        ref, label = session_range(df, ts, LONDON), "London"
    else:
        return "neu", None
    if not ref:
        return "neu", None

    hi, lo = ref
    cur = session_range(df, ts, NAMES[sess])
    if not cur:
        return "neu", None
    cur_hi, cur_lo = cur
    close_now = float(df["close"].iloc[-1])

    took_high, took_low = cur_hi > hi, cur_lo < lo
    if took_low and not took_high:
        if close_now > lo:
            return "bull", f"{label} low swept and reclaimed — sell-side stops taken"
        return "bear", f"{label} low broken and holding below"
    if took_high and not took_low:
        if close_now < hi:
            return "bear", f"{label} high swept and rejected — buy-side stops taken"
        return "bull", f"{label} high broken and holding above"
    if took_high and took_low:
        return "neu", f"both sides of the {label} range taken — no clean story"
    return "neu", f"still inside the {label} range"
