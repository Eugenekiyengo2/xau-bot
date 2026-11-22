"""Price data (data path). Default provider: Twelve Data (free tier covers XAU/USD and
the intervals we need). Swap in any provider by reimplementing fetch_ohlcv().

Notes on the free tier: it allows a limited number of API credits per minute (8 at the
time of writing). We fetch 6 timeframes, so requests are paced and we only retry on
transport/rate-limit errors — never on a parsing bug, which would burn credits fast.
Spot gold often has NO volume field; that is normal and handled.
"""
from __future__ import annotations
import time
import requests
import pandas as pd

# our timeframe -> Twelve Data interval. 3m is resampled from 1min (TD has no 3min).
TD_INTERVAL = {"1W": "1week", "1D": "1day", "4H": "4h", "30m": "30min", "15m": "15min", "3m": "1min"}
RESAMPLE = {"3m": "3min"}
OUTPUTSIZE = {"1W": 300, "1D": 400, "4H": 400, "30m": 400, "15m": 400, "3m": 400}

# seconds to wait between successive API calls, to stay under the per-minute credit cap
PACE_SECONDS = 9
_last_call_at = 0.0


class RateLimited(Exception):
    pass


def _pace():
    """Space calls out so a free-tier per-minute credit cap is never hit."""
    global _last_call_at
    wait = PACE_SECONDS - (time.time() - _last_call_at)
    if wait > 0:
        time.sleep(wait)
    _last_call_at = time.time()


def fetch_ohlcv(tf: str, cfg) -> pd.DataFrame | None:
    """Fetch candles for one timeframe. Returns a DataFrame or None (never raises)."""
    params = {
        "symbol": cfg.SYMBOL,                 # "XAU/USD"
        "interval": TD_INTERVAL[tf],
        "outputsize": OUTPUTSIZE[tf],
        "order": "ASC",
        "apikey": cfg.TWELVEDATA_API_KEY,
        "format": "JSON",
    }
    rows = None
    for attempt in range(3):
        try:
            rows = _request(params)
            break
        except RateLimited as e:
            # the only error worth waiting out: pause past the minute boundary
            if attempt == 2:
                print(f"[data] {tf} rate-limited, giving up: {e}")
                return None
            print(f"[data] {tf} rate-limited, waiting 60s...")
            time.sleep(60)
        except requests.RequestException as e:
            if attempt == 2:
                print(f"[data] {tf} network error: {e}")
                return None
            time.sleep(3 * (attempt + 1))
        except Exception as e:  # noqa - a data/parse problem; retrying wastes credits
            print(f"[data] {tf} failed: {e}")
            return None

    if not rows:
        print(f"[data] {tf}: no candles returned")
        return None

    try:
        return _to_frame(rows, tf)
    except Exception as e:  # noqa
        print(f"[data] {tf} could not be parsed: {e}")
        return None


def _request(params: dict) -> list:
    _pace()
    resp = requests.get("https://api.twelvedata.com/time_series", params=params, timeout=30)
    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError(f"non-JSON response (HTTP {resp.status_code})")

    # Twelve Data reports problems in the body, not the status code
    if isinstance(data, dict) and data.get("status") == "error":
        msg = str(data.get("message", "twelvedata error"))
        if "credits" in msg.lower() or "rate limit" in msg.lower() or data.get("code") == 429:
            raise RateLimited(msg)
        raise RuntimeError(msg)
    if resp.status_code == 429:
        raise RateLimited(f"HTTP 429: {resp.text[:120]}")
    resp.raise_for_status()

    values = data.get("values") if isinstance(data, dict) else None
    if not values:
        raise RuntimeError(f"no 'values' in response: {str(data)[:160]}")
    return values


def _to_frame(rows: list, tf: str) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for c in ("open", "high", "low", "close"):
        if c not in df.columns:
            raise RuntimeError(f"missing '{c}' column")
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # Spot gold usually has no volume. Build the column safely either way.
    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)
    else:
        df["volume"] = 0.0

    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close", "datetime"])
    df = df.sort_values("datetime").reset_index(drop=True)

    if tf in RESAMPLE:
        df = _resample(df, RESAMPLE[tf])
    return df[["datetime", "open", "high", "low", "close", "volume"]]


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
