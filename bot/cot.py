"""CFTC Commitments of Traders parser for gold (contract code 088691, the same market
the Tradingster/A1 links track). Uses the CFTC's public Disaggregated Futures-Only
history file — a documented, stable URL, no scraping.

Rule: commercials (producer/merchant + swap dealers) are the hedging 'banks'. When their
net short shrinks week-over-week, that's constructive (bullish) for gold; when it grows,
bearish. Managed-money net is reported too as a crowding gauge."""
from __future__ import annotations
import io
import zipfile
from datetime import datetime

import requests
import pandas as pd

GOLD_CODE = "088691"
HIST = "https://www.cftc.gov/files/dea/history/fut_disagg_txt_{year}.zip"


def fetch_cot(cfg) -> dict:
    year = datetime.utcnow().year
    df = _load_year(year)
    if df is None or df.empty:
        df = _load_year(year - 1)   # early January fallback
    out = {"cot_stance": "neu", "cot_commercial_net": None, "cot_mm_net": None,
           "cot_commercial_delta": None, "notes": []}
    if df is None or df.empty:
        out["notes"].append("COT: could not load CFTC history.")
        return out

    code_col = _col(df, "CFTC_Contract_Market_Code")
    date_col = _col(df, "Report_Date_as_YYYY-MM-DD") or _col(df, "Report_Date_as_YYYY_MM_DD")
    g = df[df[code_col].astype(str).str.zfill(6) == GOLD_CODE].copy()
    if g.empty:
        out["notes"].append("COT: gold rows not found.")
        return out
    g[date_col] = pd.to_datetime(g[date_col], errors="coerce")
    g = g.sort_values(date_col).tail(2)

    def net(row):
        pl = _v(row, "Prod_Merc_Positions_Long_All")
        ps = _v(row, "Prod_Merc_Positions_Short_All")
        sl = _v(row, "Swap_Positions_Long_All")
        ss = _v(row, "Swap__Positions_Short_All") or _v(row, "Swap_Positions_Short_All")
        comm = (pl + sl) - (ps + ss)
        mm = _v(row, "M_Money_Positions_Long_All") - _v(row, "M_Money_Positions_Short_All")
        return comm, mm

    latest = g.iloc[-1]
    comm, mm = net(latest)
    out["cot_commercial_net"] = comm
    out["cot_mm_net"] = mm
    if len(g) == 2:
        prev_comm, _ = net(g.iloc[0])
        delta = comm - prev_comm
        out["cot_commercial_delta"] = delta
        # commercials covering shorts (net rising) = bullish
        if delta > 0:
            out["cot_stance"] = "bull"
        elif delta < 0:
            out["cot_stance"] = "bear"
    out["notes"].append(
        f"COT {latest[date_col].date()}: commercial net {comm:,.0f} "
        f"(Δ {out['cot_commercial_delta'] or 0:,.0f}), managed-money net {mm:,.0f}.")
    return out


def _load_year(year: int):
    try:
        r = requests.get(HIST.format(year=year), timeout=60)
        r.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(r.content))
        name = next(n for n in z.namelist() if n.lower().endswith(".txt"))
        return pd.read_csv(z.open(name), low_memory=False)
    except Exception as e:  # noqa
        print(f"[cot] {year} load failed: {e}")
        return None


def _col(df, name):
    for c in df.columns:
        if c.strip() == name:
            return c
    return None


def _v(row, name):
    for c in row.index:
        if c.strip() == name:
            try:
                return float(row[c])
            except (TypeError, ValueError):
                return 0.0
    return 0.0
