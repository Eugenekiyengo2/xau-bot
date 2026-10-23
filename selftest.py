"""Offline check of the indicator + engine stack — no API keys, no network.
Builds synthetic candles (bullish HTF + a sell-side sweep on the low TFs) and prints
the signal, so you can confirm the logic before wiring real data.

  python selftest.py
"""
import numpy as np
import pandas as pd

from config import cfg
from bot import engine
from bot.models import MacroRead


def synth(drift, n=80, start=4000.0, sweep=False, seed=1):
    rng = np.random.default_rng(seed)
    o = start
    rows = []
    for i in range(n):
        step = drift + rng.normal(0, 0.6)
        c = o + step
        hi = max(o, c) + abs(rng.normal(0, 0.5))
        lo = min(o, c) - abs(rng.normal(0, 0.5))
        vol = 1000 + abs(rng.normal(0, 300))
        if sweep and i == n - 2:                 # engineer a sell-side grab then reclaim
            lo = min(o, c) - 6
            c = max(o, c) - 0.2
            vol += 3000
        rows.append((o, hi, lo, c, vol))
        o = c
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"])


reads = {
    "1W": engine.read_from_df("1W", synth(0.8, seed=11), cfg),
    "1D": engine.read_from_df("1D", synth(0.6, seed=12), cfg),
    "4H": engine.read_from_df("4H", synth(0.4, seed=13), cfg),
    "30m": engine.read_from_df("30m", synth(0.3, seed=14), cfg),
    "15m": engine.read_from_df("15m", synth(0.2, sweep=True, seed=15), cfg),
    "3m": engine.read_from_df("3m", synth(0.1, sweep=True, seed=16), cfg),
}
macro = MacroRead(cot_stance="bull", usd_stance="bull",
                  notes=["(selftest macro: COT bullish, USD weak)"])

sig = engine.build_signal(reads, macro, cfg)
print("per-TF:")
for tf, r in reads.items():
    print(f"  {tf:>3}  rsi={r.rsi!s:>6}  bias={r.bias:<4} div={r.divergence:<4} "
          f"sweep={r.sweep:<4} price={r.price}")
print(f"\nPOSTURE  {sig.posture}")
print(f"SIGNAL   {sig.bias}   conviction {sig.conviction}")
print(f"entry={sig.entry} stop={sig.stop} risk={sig.risk} tps={sig.tps}")
for r in sig.reasons:
    print(" -", r)
