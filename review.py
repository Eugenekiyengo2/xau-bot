"""Read the forward-test journal and report how the LIVE signals have actually done.

  python review.py

Unlike a backtest, nothing here benefits from hindsight: every row was written before
its outcome was known. This is the number that decides whether the strategy works.
"""
import sys

import pandas as pd

from config import cfg
from bot import journal


def main():
    df = journal.load(cfg.JOURNAL_FILE)
    print("=" * 60)
    print("FORWARD TEST — live signals, no hindsight")
    print("=" * 60)
    print(journal.summary(df))
    if df.empty:
        print("\nThe bot writes a row here each time it fires a LONG or SHORT.")
        return 0

    done = df[df["status"].astype(str) != journal.OPEN]
    if not done.empty:
        print("\nBy outcome:")
        for status, n in done["status"].value_counts().items():
            print(f"  {status:<6} {n}")
        longs = (done["dir"] == "LONG").sum()
        print(f"\nDirection: {longs} long / {len(done)-longs} short")
        rs = pd.to_numeric(done["result_r"], errors="coerce").dropna()
        if len(rs):
            eq = rs.cumsum()
            print(f"Worst drawdown: {float((eq.cummax()-eq).max()):.2f}R")

    openn = df[df["status"].astype(str) == journal.OPEN]
    if not openn.empty:
        print("\nStill open:")
        for _, r in openn.iterrows():
            print(f"  {r['opened_utc']}  {r['dir']}  entry {r['entry']}  "
                  f"stop {r['stop']}  best so far {r['best_r']}R")

    print("\nLast 10 recorded signals:")
    cols = ["opened_utc", "dir", "entry", "stop", "conviction", "status", "result_r"]
    print(df[cols].tail(10).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
