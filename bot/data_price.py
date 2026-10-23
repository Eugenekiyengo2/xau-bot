"""Price data (data path). Default provider: Twelve Data (free tier covers XAU/USD and
the intervals we need). Swap in any provider by reimplementing fetch_ohlcv()."""
from __future__ import annotations
import time
import requests
import pandas as pd

# our timeframe -> Twelve Data interval. 3m is resampled from 1min (TD has no 3min).
TD_INTERVAL = {"1W": "1week", "1D": "1day", "4H": "4h", "30m": "30min", "15m": "15min", "3m": "1min"}
RESAMPLE = {"3m": "3min"}
OUTPUTSIZE = {"1W": 300, "1D": 400, "4H": 400, "30m": 400, "15m": 400, "3m": 400}


def fetch_ohlcv(tf: str, cfg) -> pd.DataFrame | None:
    interval = TD_INTERVAL[tf]
    params = {
        "symbol": cfg.SYMBOL,                 # "XAU/USD"
        "interval": interval,
        "outputsize": OUTPUTSIZE[tf],
        "order": "ASC",
        "apikey": cfg.TWELVEDATA_API_KEY,
        "format": "JSON",
    }
    for attempt in range(3):
        try:
            resp = requests.get("https://api.twelvedata.com/time_series", params=params, timeout=30)
            data = resp.json()
            if data.get("status") == "error":
                raise RuntimeError(data.get("message", "twelvedata error"))
            rows = data["values"]
            df = pd.DataFrame(rows)
            for c in ("open", "high", "low", "close"):
                df[c] = pd.to_numeric(df[c], errors="coerce")
            df["volume"] = pd.to_numeric(df.get("volume", 0), errors="coerce").fillna(0)
            df["datetime"] = pd.to_datetime(df["datetime"])
            df = df.sort_values("datetime").reset_index(drop=True)
            if tf in RESAMPLE:
                df = _resample(df, RESAMPLE[tf])
            return df[["datetime", "open", "high", "low", "close", "volume"]]
        except Exception as e:  # noqa
            if attempt == 2:
                print(f"[data] {tf} failed: {e}")
                return None
            time.sleep(2 * (attempt + 1))


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    g = df.set_index("datetime").resample(rule)
    out = pd.DataFrame({
        "open": g["open"].first(),
        "high": g["high"].max(),
        "low": g["low"].min(),
        "close": g["close"].last(),
        "volume": g["volume"].sum(),
    }).dropna().reset_index()
    return out
