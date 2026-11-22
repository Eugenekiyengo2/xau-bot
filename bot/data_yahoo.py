"""Deep history from Yahoo Finance — free, no API key, no per-minute limit.

Why this exists: the Twelve Data free tier caps intraday history at roughly 50 days,
which yields only a handful of signals — too few to judge a strategy. Yahoo gives
about two years of hourly data and decades of daily, so a backtest can reach a
sample size that actually means something.

Yahoo's own ceilings per interval (these are hard limits, not ours):
    1m    7 days        15m/30m   60 days
    1h    730 days      1d/1wk    decades

So 1W, 1D and 4H (resampled from 1h) get deep history; 15m and 30m stay shallow.
To backtest over years, trigger entries from 4H or 1D rather than 15m.

Default symbol is GC=F (COMEX gold futures). It tracks spot closely; the basis
shifts slowly and barely affects R-multiples, which is what the backtest measures.
"""
from __future__ import annotations
import pandas as pd

# our timeframe -> (yahoo interval, period to request, resample rule or None)
YF_SPEC = {
    "1W": ("1wk", "max", None),
    "1D": ("1d", "max", None),
    "4H": ("1h", "730d", "4h"),     # 4h isn't native; build it from hourly
    "30m": ("30m", "60d", None),
    "15m": ("15m", "60d", None),
    "3m": ("5m", "60d", None),      # 3m isn't offered; 5m is the closest
}
DEFAULT_TICKER = "GC=F"


def fetch_ohlcv(tf: str, cfg) -> pd.DataFrame | None:
    try:
        import yfinance as yf
    except ImportError:
        print("[yahoo] yfinance is not installed — add it to requirements.txt")
        return None
    if tf not in YF_SPEC:
        print(f"[yahoo] no mapping for {tf}")
        return None

    interval, period, rule = YF_SPEC[tf]
    ticker = getattr(cfg, "YAHOO_TICKER", DEFAULT_TICKER)
    try:
        raw = yf.download(ticker, interval=interval, period=period,
                          auto_adjust=False, progress=False, threads=False)
    except Exception as e:  # noqa
        print(f"[yahoo] {tf} download failed: {e}")
        return None
    if raw is None or raw.empty:
        print(f"[yahoo] {tf}: no data returned for {ticker}")
        return None

    try:
        df = _normalise(raw)
    except Exception as e:  # noqa
        print(f"[yahoo] {tf} could not be parsed: {e}")
        return None
    if rule:
        df = _resample(df, rule)
    return df


def _normalise(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    # a single-ticker download can still come back with MultiIndex columns
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.reset_index()
    stamp = next((c for c in ("Datetime", "Date", "index") if c in df.columns), df.columns[0])
    out = pd.DataFrame({
        "datetime": pd.to_datetime(df[stamp], errors="coerce", utc=True).dt.tz_localize(None),
        "open": pd.to_numeric(df["Open"], errors="coerce"),
        "high": pd.to_numeric(df["High"], errors="coerce"),
        "low": pd.to_numeric(df["Low"], errors="coerce"),
        "close": pd.to_numeric(df["Close"], errors="coerce"),
        "volume": pd.to_numeric(df.get("Volume", 0), errors="coerce").fillna(0.0),
    })
    return out.dropna(subset=["datetime", "open", "high", "low", "close"]) \
              .sort_values("datetime").reset_index(drop=True)


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    g = df.set_index("datetime").resample(rule)
    return pd.DataFrame({
        "open": g["open"].first(), "high": g["high"].max(),
        "low": g["low"].min(), "close": g["close"].last(),
        "volume": g["volume"].sum(),
    }).dropna().reset_index()
