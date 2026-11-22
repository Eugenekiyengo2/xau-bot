"""Technical indicators, exactly to the Confluence Desk spec.

RSI: length 9, source close, Wilder smoothing.
RSI-MA: SMA length 14 of the RSI.
Bollinger Bands on the RSI: basis = SMA(14) of RSI, dev = 2 * stdev(14) of RSI.
Liquidity Swings (LuxAlgo approximation): pivot highs/lows over a lookback using the
wick extremes, with volume tagged to each level and sweep (liquidity-grab) detection.
ATR: length 14, Wilder, used for stop buffers.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def rsi_wilder(close, length: int = 9) -> np.ndarray:
    close = np.asarray(close, dtype=float)
    n = len(close)
    out = np.full(n, np.nan)
    if n < length + 1:
        return out
    delta = np.diff(close)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = gain[:length].mean()
    avg_loss = loss[:length].mean()
    out[length] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    for i in range(length + 1, n):
        avg_gain = (avg_gain * (length - 1) + gain[i - 1]) / length
        avg_loss = (avg_loss * (length - 1) + loss[i - 1]) / length
        out[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def atr_wilder(df: pd.DataFrame, length: int = 14) -> np.ndarray:
    high, low, close = df["high"].values, df["low"].values, df["close"].values
    n = len(close)
    tr = np.empty(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    out = np.full(n, np.nan)
    if n < length:
        return out
    prev = tr[:length].mean()
    out[length - 1] = prev
    for i in range(length, n):
        prev = (prev * (length - 1) + tr[i]) / length
        out[i] = prev
    return out


def rsi_bands(rsi: np.ndarray, ma_len: int = 14, mult: float = 2.0):
    """Returns (ma, upper, lower) series for the RSI, matching TradingView's RSI-MA+BB."""
    s = pd.Series(rsi)
    ma = s.rolling(ma_len).mean()
    std = s.rolling(ma_len).std(ddof=0)  # population stdev, like ta.stdev
    return ma.values, (ma + mult * std).values, (ma - mult * std).values


def find_pivots(df: pd.DataFrame, length: int = 14):
    """Pivot highs/lows using wick extremes. A bar i is a pivot high if its high is the
    strict max over [i-length, i+length]; mirror for lows. Returns two lists of dicts."""
    high, low, vol = df["high"].values, df["low"].values, df.get("volume", pd.Series(np.zeros(len(df)))).values
    n = len(df)
    highs, lows = [], []
    for i in range(length, n - length):
        window = slice(i - length, i + length + 1)
        if high[i] == high[window].max() and (high[window] == high[i]).sum() == 1:
            highs.append({"i": i, "level": float(high[i]), "vol": float(vol[i])})
        if low[i] == low[window].min() and (low[window] == low[i]).sum() == 1:
            lows.append({"i": i, "level": float(low[i]), "vol": float(vol[i])})
    return highs, lows


def detect_sweep(df: pd.DataFrame, last_high: dict | None, last_low: dict | None,
                 lookback: int = 1, atr: float | None = None, max_drift_atr: float = 2.0):
    """Find a liquidity grab in the last `lookback` closed bars.

    A sell-side sweep is a bar that traded below a swing low and closed back above it;
    a buy-side sweep is the mirror. The original version only ever inspected the final
    bar, which meant a sweep two candles ago was invisible — and since the real entry
    comes on the pullback AFTER the grab, that threw away most valid setups.

    Scanning back has one hazard: if price has since run a long way from the swept
    level, entering now means a huge stop and a poor reward-to-risk. So a sweep is only
    returned while price is still within `max_drift_atr` ATRs of the level it swept.
    """
    n = len(df)
    span = max(1, min(int(lookback), n))
    close_now = float(df["close"].iloc[-1])

    for back in range(span):                     # newest first: the freshest grab wins
        bar = df.iloc[n - 1 - back]
        if last_low is not None:
            lvl = last_low["level"]
            if bar["low"] < lvl and bar["close"] > lvl:
                if _still_near(close_now, lvl, atr, max_drift_atr):
                    return "bull", lvl
        if last_high is not None:
            lvl = last_high["level"]
            if bar["high"] > lvl and bar["close"] < lvl:
                if _still_near(close_now, lvl, atr, max_drift_atr):
                    return "bear", lvl
    return "none", None


def _still_near(price: float, level: float, atr: float | None, max_drift_atr: float) -> bool:
    """Has price stayed close enough to the swept level for the trade to make sense?"""
    if not atr or atr <= 0:
        return True
    return abs(price - level) <= max_drift_atr * atr


def detect_divergence(df: pd.DataFrame, rsi: np.ndarray, pivots) -> str:
    """Regular divergence on the last two price pivots vs RSI at those bars."""
    highs, lows = pivots
    if len(lows) >= 2:
        a, b = lows[-2], lows[-1]
        if b["level"] < a["level"] and rsi[b["i"]] > rsi[a["i"]]:
            return "bull"  # price lower-low, RSI higher-low
    if len(highs) >= 2:
        a, b = highs[-2], highs[-1]
        if b["level"] > a["level"] and rsi[b["i"]] < rsi[a["i"]]:
            return "bear"  # price higher-high, RSI lower-high
    return "none"


# ---------------------------------------------------------------- ICT structures

def detect_mss(df: pd.DataFrame, direction: str, extreme_idx: int | None,
               pre: int = 6) -> bool:
    """Market Structure Shift: has price broken the structure that preceded the grab?

    After a liquidity grab the move only confirms when price closes back beyond the
    minor high (for a long) that the market was failing at just before the grab. The
    comparison is deliberately LOCAL - against the last few bars before the wick, not
    against the whole prior swing - because a shift of structure is a short-term
    character change, and demanding a break of the major high would mean entering only
    after the move is already over.
    """
    n = len(df)
    if extreme_idx is None or extreme_idx >= n - 1:
        return False
    close_now = float(df["close"].iloc[-1])
    lo = max(0, extreme_idx - pre)
    seg = df.iloc[lo:extreme_idx + 1]
    if seg.empty:
        return False
    if direction == "bull":
        return close_now > float(seg["high"].max())
    if direction == "bear":
        return close_now < float(seg["low"].min())
    return False


def find_fvg(df: pd.DataFrame, direction: str, lookback: int = 20, after: int | None = None):
    """Fair Value Gap: a three-bar imbalance price tends to return to.

    Bullish: bar i-2's high sits BELOW bar i's low, leaving an untraded gap between
    them. Price commonly retraces into that gap before continuing, which is where a
    precise entry lives — close to invalidation, so the stop is small and the reward
    per unit of risk is large.

    Returns (gap_low, gap_high) for the newest unfilled gap, or None.
    """
    n = len(df)
    if n < 3:
        return None
    hi, lo = df["high"].values, df["low"].values
    start = max(2, n - lookback)
    if after is not None:
        start = max(start, int(after) + 2)
    rng = range(start, n) if after is not None else range(n - 1, start - 1, -1)
    for i in rng:
        if direction == "bull" and hi[i - 2] < lo[i]:
            gap_lo, gap_hi = float(hi[i - 2]), float(lo[i])
            if lo[i:].min() > gap_lo:                 # not yet filled through
                return gap_lo, gap_hi
        if direction == "bear" and lo[i - 2] > hi[i]:
            gap_lo, gap_hi = float(hi[i]), float(lo[i - 2])
            if hi[i:].max() < gap_hi:
                return gap_lo, gap_hi
    return None


def sweep_extreme(df: pd.DataFrame, direction: str, lookback: int = 30):
    """The wick that did the grabbing, and where it sits. That price is the natural
    invalidation point: if it trades through, the grab failed."""
    n = len(df)
    start = max(0, n - lookback)
    w = df.iloc[start:]
    if w.empty:
        return None, None
    if direction == "bull":
        pos = int(w["low"].values.argmin())
        return float(w["low"].iloc[pos]), start + pos
    pos = int(w["high"].values.argmax())
    return float(w["high"].iloc[pos]), start + pos


def in_killzone(ts, zones) -> bool:
    """ICT killzones: the London and New York opens, when institutional flow is
    heaviest. Outside them, moves are thinner and sweeps mean less."""
    if not zones:
        return True
    try:
        hour = pd.Timestamp(ts).hour
    except Exception:  # noqa
        return True
    return any(a <= hour < b for a, b in zones)
