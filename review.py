"""Read the forward-test journals and report how the LIVE signals have actually done.

  python review.py

One journal per market (XAUUSD_journal.csv, EURUSD_journal.csv, ...). Every row was
written before its outcome was known, so unlike a backtest nothing here benefits from
hindsight. This is the number that decides whether the strategy works.
"""
import glob
import sys

import pandas as pd

from bot import journal

SUFFIX = "_journal.csv"


def report(name: str, df: pd.DataFrame):
    print(f"\n{'-'*60}\n{name}\n{'-'*60}")
    print(journal.summary(df))
    if df.empty:
        return
    done = df[df["status"].astype(str) != journal.OPEN]
    if not done.empty:
        counts = "  ".join(f"{k}:{v}" for k, v in done["status"].value_counts().items())
        longs = (done["dir"] == "LONG").sum()
        print(f"Outcomes: {counts}")
        print(f"Direction: {longs} long / {len(done)-longs} short")
        rs = pd.to_numeric(done["result_r"], errors="coerce").dropna()
        if len(rs):
            eq = rs.cumsum()
            print(f"Worst drawdown: {float((eq.cummax()-eq).max()):.2f}R")
    openn = df[df["status"].astype(str) == journal.OPEN]
    for _, r in openn.iterrows():
        print(f"  OPEN  {r['opened_utc']}  {r['dir']}  entry {r['entry']}  "
              f"stop {r['stop']}  best so far {r['best_r']}R")


def main():
    paths = sorted(glob.glob("*" + SUFFIX))
    print("=" * 60)
    print("FORWARD TEST — live signals, no hindsight")
    print("=" * 60)
    if not paths:
        print("\nNo journals yet. The bot writes one row per market each time it")
        print("fires a LONG or SHORT, so nothing appears until a real signal lands.")
        return 0

    everything = []
    for p in paths:
        market = p[:-len(SUFFIX)]
        df = journal.load(p)
        report(market, df)
        if not df.empty:
            df = df.copy()
            df["market"] = market
            everything.append(df)

    if len(everything) > 1:
        allrows = pd.concat(everything, ignore_index=True)
        print(f"\n{'='*60}\nALL MARKETS COMBINED\n{'='*60}")
        print(journal.summary(allrows))
        done = allrows[allrows["status"].astype(str) != journal.OPEN]
        if not done.empty:
            rs = pd.to_numeric(done["result_r"], errors="coerce").dropna()
            print("\nPer market:")
            for m, g in done.groupby("market"):
                v = pd.to_numeric(g["result_r"], errors="coerce").dropna()
                if len(v):
                    print(f"  {m:<10} {len(v):>3} trades   {v.sum():+7.2f}R   avg {v.mean():+.2f}R")
            if len(rs) < 30:
                print(f"\nStill only {len(rs)} resolved trades in total. Needs 30+ before")
                print("any of these numbers mean anything.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
