"""The macro inputs that were declared but never actually computed.

Three things the engine has always read and nothing ever set:

  usd_stance   — gold and the majors move against the dollar, so dollar direction is
                 a real input rather than a decoration. Derived from EUR/USD, which
                 is the dollar's largest counterpart: EUR/USD rising means the dollar
                 is weakening, which is supportive for gold.
  vol_regime   — how today's range compares with its own recent history. Wide ranges
                 mean wider stops and more whipsaw, so conviction should fall.
  sentiment    — the retail crowd, to be faded at extremes. No free feed publishes
                 this reliably, so it stays a value you set yourself; left alone it
                 contributes nothing rather than guessing.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def usd_stance(cfg) -> tuple:
    """Dollar direction, expressed as its effect on gold and the majors.

    Returns ("bull"|"bear"|"neu", note) where "bull" means a WEAK dollar, which is
    the supportive case, matching how the engine reads every other bullish input.
    """
    try:
        from . import data_price
        prev = cfg.SYMBOL
        cfg.SYMBOL = getattr(cfg, "USD_PROXY", "EUR/USD")
        try:
            df = data_price.fetch_ohlcv("1D", cfg)
        finally:
            cfg.SYMBOL = prev
    except Exception as e:  # noqa
        return "neu", f"USD: unavailable ({e})"
    if df is None or len(df) < 60:
        return "neu", "USD: not enough history"

    close = df["close"].astype(float)
    ma50 = close.rolling(50).mean().iloc[-1]
    last = float(close.iloc[-1])
    chg = (last / float(close.iloc[-21]) - 1) * 100 if len(close) > 21 else 0.0

    if np.isnan(ma50):
        return "neu", "USD: not enough history"
    if last > ma50 and chg > 0.3:
        return "bull", f"USD weak (EUR/USD {chg:+.1f}% over a month, above its 50-day)"
    if last < ma50 and chg < -0.3:
        return "bear", f"USD strong (EUR/USD {chg:+.1f}% over a month, below its 50-day)"
    return "neu", f"USD flat (EUR/USD {chg:+.1f}% over a month)"


def vol_regime(frames: dict) -> tuple:
    """Where current volatility sits within its own recent range."""
    df = frames.get("1D") if frames else None
    if df is None or len(df) < 60:
        return "normal", None
    from . import indicators as ind
    atr = pd.Series(ind.atr_wilder(df, 14)).dropna()
    if len(atr) < 40:
        return "normal", None
    now, pct = float(atr.iloc[-1]), float((atr.tail(120) < atr.iloc[-1]).mean() * 100)
    if pct >= 85:
        return "high", f"volatility elevated ({pct:.0f}th percentile, ATR {now:.2f})"
    if pct <= 15:
        return "low", f"volatility compressed ({pct:.0f}th percentile, ATR {now:.2f})"
    return "normal", f"volatility normal ({pct:.0f}th percentile)"


def sentiment_stance(cfg) -> tuple:
    """Retail positioning, to be faded. Set RETAIL_LONG_PCT yourself from Myfxbook
    or your broker; with nothing set this contributes nothing rather than inventing."""
    pct = getattr(cfg, "RETAIL_LONG_PCT", None)
    try:
        pct = float(pct)
    except (TypeError, ValueError):
        return "neu", None
    if pct >= 65:
        return "bear", f"crowd {pct:.0f}% long — contrarian risk to the downside"
    if pct <= 35:
        return "bull", f"crowd {100-pct:.0f}% short — contrarian fuel to the upside"
    return "neu", f"retail positioning balanced ({pct:.0f}% long)"
