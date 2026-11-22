"""Position size from MEASURED performance, not from hope.

  python sizing.py 1000          # your account balance

Large position sizes are not reckless in themselves — with a 2.3:1 payoff the
growth-optimal bet is genuinely large, and that is what makes big weekly returns
arithmetically possible. What makes them reckless is sizing for an edge you have
assumed rather than measured: betting 21% when the true hit rate is ten points below
your estimate ruins the account about 95% of the time.

So this reads the live journal, works out what the edge ACTUALLY is, and sizes from
that. With too few trades to know, it says so and recommends the minimum.
"""
from __future__ import annotations
import glob
import sys

import numpy as np
import pandas as pd

from bot import journal

MIN_SAMPLE = 30
FLOOR_RISK = 0.005          # 0.5% while the edge is unmeasured


def gather() -> pd.DataFrame:
    frames = []
    for p in sorted(glob.glob("*_journal.csv")):
        df = journal.load(p)
        if not df.empty:
            df = df.copy()
            df["market"] = p.replace("_journal.csv", "")
            frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def kelly(p: float, win: float, loss: float) -> float:
    """f* = p - q/b. Negative means the edge is against you: do not bet."""
    if win <= 0 or loss >= 0:
        return 0.0
    b = win / abs(loss)
    return p - (1 - p) / b


def main(balance: float) -> int:
    print("=" * 62)
    print(f"POSITION SIZE FROM MEASURED EDGE — account ${balance:,.2f}")
    print("=" * 62)

    df = gather()
    done = df[df["status"].astype(str) != journal.OPEN] if not df.empty else pd.DataFrame()
    rs = pd.to_numeric(done["result_r"], errors="coerce").dropna() if not done.empty else pd.Series(dtype=float)
    n = len(rs)

    if n < MIN_SAMPLE:
        print(f"\nResolved trades so far: {n}. Need {MIN_SAMPLE} before the edge is")
        print("measurable at all, so there is nothing to size from yet.")
        print(f"\n  Recommended risk: {FLOOR_RISK:.1%}  =  ${balance*FLOOR_RISK:,.2f} per trade")
        print("\nThis is deliberately small. Sizing up before the hit rate is known is")
        print("the single fastest way to lose the account, and the journal is only")
        print(f"{MIN_SAMPLE - n} trades away from telling you something real.")
        return 0

    wins = rs[rs > 0]
    losses = rs[rs <= 0]
    p = len(wins) / n
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else -1.0
    f_full = kelly(p, avg_win, avg_loss)
    se = rs.std(ddof=1) / np.sqrt(n)
    lo = rs.mean() - 2 * se

    print(f"\nMeasured over {n} live trades:")
    print(f"  hit rate      {p:>7.1%}")
    print(f"  average win   {avg_win:>+7.2f}R")
    print(f"  average loss  {avg_loss:>+7.2f}R")
    print(f"  expectancy    {rs.mean():>+7.2f}R   (95% range {lo:+.2f} to {rs.mean()+2*se:+.2f})")

    if lo <= 0:
        print("\n  The confidence range still includes zero — the edge is not yet")
        print("  demonstrated, whatever the average says.")
        print(f"\n  Recommended risk: {FLOOR_RISK:.1%}  =  ${balance*FLOOR_RISK:,.2f} per trade")
        return 0
    if f_full <= 0:
        print("\n  Kelly is negative: these trades lose money at any size.")
        print("\n  Recommended risk: 0%. Do not trade this until it changes.")
        return 0

    print(f"\n  full Kelly    {f_full:>7.1%}  (growth-optimal, ~96% drawdowns, ~11% ruin)")
    print(f"  half Kelly    {f_full/2:>7.1%}  <- recommended")
    print(f"  quarter Kelly {f_full/4:>7.1%}  (conservative)")
    print(f"\n  Recommended risk: {f_full/2:.1%}  =  ${balance*f_full/2:,.2f} per trade")
    print("\nHalf Kelly gives up roughly a quarter of the growth for far shallower")
    print("drawdowns, and it survives your hit rate being somewhat worse than measured.")
    print("Full Kelly does not. Re-run this as the journal grows; the number moves.")
    return 0


if __name__ == "__main__":
    bal = float(sys.argv[1]) if len(sys.argv) > 1 else 1000.0
    sys.exit(main(bal))
