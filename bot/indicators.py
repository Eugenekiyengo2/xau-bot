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


def detect_sweep(df: pd.DataFrame, last_high: dict | None, last_low: dict | None):
    """On the most recent closed bar: sweep of sell-side (below last swing low, closes back
    above) -> 'bull'; sweep of buy-side (above last swing high, closes back below) -> 'bear'."""
    bar = df.iloc[-1]
    if last_low and bar["low"] < last_low["level"] and bar["close"] > last_low["level"]:
        return "bull", last_low["level"]
    if last_high and bar["high"] > last_high["level"] and bar["close"] < last_high["level"]:
        return "bear", last_high["level"]
    return "none", None


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
