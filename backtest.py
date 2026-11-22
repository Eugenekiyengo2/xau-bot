"""Backtest the Confluence engine on historical XAUUSD data.

It replays history bar by bar on an execution timeframe, rebuilding the SAME signal
the live bot would have produced at that moment (same engine.py, same indicators),
then simulates the resulting trade against later price action.

  python backtest.py                      # 15m execution, cached data
  python backtest.py --exec-tf 30m        # slower timeframe
  python backtest.py --bars 3000          # more history
  python backtest.py --with-cot           # include real CFTC COT history
  python backtest.py --refresh            # re-download price data
  python backtest.py --csv trades.csv     # dump every trade

Guards against lying to you:
  * No lookahead. At each step a higher timeframe only contributes bars that had
    already CLOSED at that moment, so the weekly bar can't leak Friday's range
    into a Wednesday decision.
  * Conservative fills. If a bar touches both the stop and a target, the stop is
    assumed hit first.
  * Costs. A spread/commission is deducted from every trade.

What it cannot model: slippage in fast news moves, weekend gaps against an open
position, and partial fills. Treat the output as a sanity check on the rules,
not a promise of returns.
"""
from __future__ import annotations
import argparse
import os
import sys

import numpy as np
import pandas as pd

from config import cfg
from bot import engine
from bot.models import MacroRead

CACHE_DIR = "backtest_data"
ALL_TFS = ("1W", "1D", "4H", "30m", "15m", "3m")
BAR_LEN = {"1W": pd.Timedelta(days=7), "1D": pd.Timedelta(days=1),
           "4H": pd.Timedelta(hours=4), "30m": pd.Timedelta(minutes=30),
           "15m": pd.Timedelta(minutes=15), "3m": pd.Timedelta(minutes=3)}
SLICE = 320          # bars of context handed to the engine (plenty for RSI 9 / SMA 14 / swings 14)
MIN_SAMPLE = 30      # below this, no expectancy claim is made


# ----------------------------------------------------------------- data

def load_prices(tfs, bars: int, refresh: bool, source: str) -> dict[str, pd.DataFrame]:
    os.makedirs(CACHE_DIR, exist_ok=True)
    out = {}
    for tf in tfs:
        path = os.path.join(CACHE_DIR, f"{source}_{tf}.csv")
        if os.path.exists(path) and not refresh:
            df = pd.read_csv(path, parse_dates=["datetime"])
            print(f"[data] {tf:>3}: {len(df)} bars (cached)")
        else:
            if source == "yahoo":
                from bot import data_yahoo
                df = data_yahoo.fetch_ohlcv(tf, cfg)
            else:
                from bot import data_price
                size = data_price.OUTPUTSIZE.copy()
                size[tf] = bars
                data_price.OUTPUTSIZE = size
                df = data_price.fetch_ohlcv(tf, cfg)
            if df is None or df.empty:
                print(f"[data] {tf:>3}: unavailable — skipped")
                continue
            df.to_csv(path, index=False)
            span = f"{df['datetime'].iloc[0]:%Y-%m-%d} to {df['datetime'].iloc[-1]:%Y-%m-%d}"
            print(f"[data] {tf:>3}: {len(df)} bars ({span})")
        if df is not None and not df.empty:
            out[tf] = df.sort_values("datetime").reset_index(drop=True)
    return out


def load_cot_history(start, end) -> pd.DataFrame | None:
    """Real CFTC commercial positioning per report date, for the years covered."""
    try:
        from bot import cot as cotmod
    except Exception:  # noqa
        return None
    frames = []
    for year in range(int(start.year), int(end.year) + 1):
        df = cotmod._load_year(year)
        if df is None or df.empty:
            continue
        code_col = cotmod._col(df, "CFTC_Contract_Market_Code")
        date_col = (cotmod._col(df, "Report_Date_as_YYYY-MM-DD")
                    or cotmod._col(df, "Report_Date_as_YYYY_MM_DD"))
        if not code_col or not date_col:
            continue
        g = df[df[code_col].astype(str).str.zfill(6) == cotmod.GOLD_CODE].copy()
        if g.empty:
            continue
        g["_date"] = pd.to_datetime(g[date_col], errors="coerce")
        g["_net"] = g.apply(lambda r: (cotmod._v(r, "Prod_Merc_Positions_Long_All")
                                       + cotmod._v(r, "Swap_Positions_Long_All"))
                                      - (cotmod._v(r, "Prod_Merc_Positions_Short_All")
                                         + (cotmod._v(r, "Swap__Positions_Short_All")
                                            or cotmod._v(r, "Swap_Positions_Short_All"))), axis=1)
        frames.append(g[["_date", "_net"]])
    if not frames:
        return None
    hist = pd.concat(frames).dropna().sort_values("_date").reset_index(drop=True)
    hist["_delta"] = hist["_net"].diff()
    hist["stance"] = np.where(hist["_delta"] > 0, "bull",
                              np.where(hist["_delta"] < 0, "bear", "neu"))
    return hist


def cot_as_of(hist: pd.DataFrame | None, when) -> str:
    if hist is None or hist.empty:
        return "neu"
    past = hist[hist["_date"] <= when]
    return "neu" if past.empty else str(past.iloc[-1]["stance"])


# ----------------------------------------------------------------- replay

def completed_slice(df: pd.DataFrame, tf: str, now) -> pd.DataFrame:
    """Only bars whose close time is at or before `now` — this is the no-lookahead rule."""
    closed = df[df["datetime"] + BAR_LEN[tf] <= now]
    return closed.tail(SLICE)


def run(args):
    # The chosen timeframe is what fires entries. Without this the engine would keep
    # using its hardcoded 3m/15m triggers and "--exec-tf 4H" would silently test
    # something else entirely.
    cfg.EXEC_TFS = (args.exec_tf,)
    cfg.USE_TREND_FILTER = not args.no_trend_filter
    print(f"[conf] entries triggered by: {args.exec_tf}   source: {args.source}"
          f"   trend filter: {'ON' if cfg.USE_TREND_FILTER else 'OFF'}")

    tfs = [t for t in ALL_TFS if t != "3m" or args.exec_tf == "3m"]
    prices = load_prices(tfs, args.bars, args.refresh, args.source)
    if args.exec_tf not in prices:
        print(f"\nNo data for execution timeframe {args.exec_tf}. "
              f"With --source twelvedata you need a valid TWELVEDATA_API_KEY; "
              f"--source yahoo needs no key.")
        return 1

    ex = prices[args.exec_tf]
    cot_hist = load_cot_history(ex["datetime"].iloc[0], ex["datetime"].iloc[-1]) if args.with_cot else None
    if args.with_cot:
        print(f"[cot ] {0 if cot_hist is None else len(cot_hist)} weekly reports loaded")

    cache: dict[str, tuple] = {}          # tf -> (last closed bar ts, TFRead)
    trades = []
    open_trade = None
    start = max(60, cfg.RSI_LEN + cfg.MA_LEN + 2)

    for i in range(start, len(ex) - 1):
        bar = ex.iloc[i]
        now = bar["datetime"]

        # --- manage an open position on this bar before looking for a new one ---
        if open_trade is not None:
            closed = update_trade(open_trade, ex.iloc[i])
            if closed:
                trades.append(open_trade)
                open_trade = None
            continue

        # --- rebuild each timeframe's reading, recomputing only when a bar closed ---
        reads = {}
        for tf, df in prices.items():
            sl = completed_slice(df, tf, now)
            if sl.empty:
                continue
            stamp = sl["datetime"].iloc[-1]
            hit = cache.get(tf)
            if hit and hit[0] == stamp:
                reads[tf] = hit[1]
            else:
                r = engine.read_from_df(tf, sl.reset_index(drop=True), cfg)
                cache[tf] = (stamp, r)
                reads[tf] = r

        if args.exec_tf in reads:
            reads[args.exec_tf].price = float(bar["close"])   # price as of this moment

        macro = MacroRead(cot_stance=cot_as_of(cot_hist, now) if args.with_cot else "neu")
        sig = engine.build_signal(reads, macro, cfg)

        if sig.bias in ("LONG", "SHORT") and sig.entry and sig.stop and sig.risk and sig.risk > 0:
            open_trade = {
                "idx": i,
                "opened": now, "dir": sig.bias, "entry": sig.entry, "stop": sig.stop,
                "tps": sig.tps, "risk": sig.risk, "conviction": sig.conviction,
                "posture": sig.posture, "hit": [], "closed": None, "exit": None,
                "bars": 0, "mfe_r": 0.0,
            }

    if open_trade is not None:
        open_trade["exit"] = "still open at end of data"
        trades.append(open_trade)

    args._benchmark = random_benchmark(ex, trades, args.cost_r)
    report(trades, args)
    return 0


def update_trade(t: dict, bar) -> bool:
    """Advance one bar. Returns True when the position is finished."""
    t["bars"] += 1
    hi, lo = float(bar["high"]), float(bar["low"])
    long = t["dir"] == "LONG"

    # best excursion so far, in R
    fav = (hi - t["entry"]) if long else (t["entry"] - lo)
    t["mfe_r"] = max(t["mfe_r"], fav / t["risk"])

    stop_hit = lo <= t["stop"] if long else hi >= t["stop"]
    for n, tp in enumerate(t["tps"], start=1):
        reached = hi >= tp if long else lo <= tp
        if reached and n not in t["hit"]:
            t["hit"].append(n)

    # conservative: a bar that touches both is treated as the stop first
    if stop_hit:
        t["closed"], t["exit"] = bar["datetime"], "stop"
        return True
    if 3 in t["hit"]:
        t["closed"], t["exit"] = bar["datetime"], "tp3"
        return True
    return False


# ----------------------------------------------------------------- results

def policy_r(t: dict, target: int, cost_r: float) -> float | None:
    """R returned if the plan were to exit wholly at TP`target`."""
    if t["exit"] == "still open at end of data":
        return None
    if any(h >= target for h in t["hit"]):
        tp = t["tps"][target - 1]
        r = abs(tp - t["entry"]) / t["risk"]
        return r - cost_r
    return -1.0 - cost_r


def scaled_r(t: dict, cost_r: float) -> float | None:
    """50% off at TP1 (stop to breakeven), 30% at TP2, 20% at TP3."""
    if t["exit"] == "still open at end of data":
        return None
    if not t["hit"]:
        return -1.0 - cost_r
    r = 0.0
    r += 0.5 * (abs(t["tps"][0] - t["entry"]) / t["risk"])
    if 2 in t["hit"]:
        r += 0.3 * (abs(t["tps"][1] - t["entry"]) / t["risk"])
    else:
        r += 0.0                     # remainder stopped at breakeven
    if 3 in t["hit"]:
        r += 0.2 * (abs(t["tps"][2] - t["entry"]) / t["risk"])
    return r - cost_r


def random_benchmark(ex: pd.DataFrame, trades: list, cost_r: float, runs: int = 400) -> dict | None:
    """Do the ENTRIES add value, or would any entry have worked?

    This is the question a profitable-looking long-only backtest cannot answer on its
    own: if the market rose tenfold, buying at random dates also makes money. So we
    re-run the strategy's own trade count, direction mix and exit rules from RANDOM
    dates, several hundred times, and see where the real result falls in that
    distribution. Beating 95% of random entries is evidence of timing skill. Landing
    mid-pack means the trend did the work, not the signal.
    """
    from bot import indicators as ind
    usable = [t for t in trades if t.get("idx") is not None]
    if len(usable) < 5:
        return None

    atr = ind.atr_wilder(ex, 14)
    # express each real stop as a multiple of ATR, so random trades are risked alike
    ratios = [t["risk"] / atr[t["idx"]] for t in usable
              if t["idx"] < len(atr) and atr[t["idx"]] and not np.isnan(atr[t["idx"]])]
    if not ratios:
        return None
    ratio = float(np.median(ratios))
    dirs = [t["dir"] for t in usable]
    lo, hi = 60, len(ex) - 2
    if hi <= lo:
        return None

    rng = np.random.default_rng(12345)
    means = []
    for _ in range(runs):
        rs = []
        for d in dirs:
            i = int(rng.integers(lo, hi))
            a = atr[i]
            if a is None or np.isnan(a) or a <= 0:
                continue
            entry = float(ex["close"].iloc[i])
            risk = ratio * a
            stop = entry - risk if d == "LONG" else entry + risk
            sign = 1 if d == "LONG" else -1
            tps = [entry + sign * risk, entry + sign * 2 * risk, entry + sign * 3 * risk]
            t = {"dir": d, "entry": entry, "stop": stop, "tps": tps, "risk": risk,
                 "hit": [], "closed": None, "exit": None, "bars": 0, "mfe_r": 0.0}
            for j in range(i + 1, len(ex)):
                if update_trade(t, ex.iloc[j]):
                    break
            if t["exit"] is None:
                t["exit"] = "end"
            r = scaled_r(t, cost_r)
            if r is not None:
                rs.append(r)
        if rs:
            means.append(float(np.mean(rs)))
    if not means:
        return None
    return {"runs": len(means), "mean": float(np.mean(means)),
            "p5": float(np.percentile(means, 5)), "p95": float(np.percentile(means, 95)),
            "dist": np.array(means)}


def stats(rs: list[float]) -> dict:
    rs = [r for r in rs if r is not None]
    if not rs:
        return {}
    arr = np.array(rs)
    wins, losses = arr[arr > 0], arr[arr <= 0]
    equity = np.cumsum(arr)
    peak = np.maximum.accumulate(equity)
    return {
        "trades": len(arr),
        "win_rate": 100.0 * len(wins) / len(arr),
        "total_r": arr.sum(),
        "avg_r": arr.mean(),
        "max_dd_r": float((peak - equity).max()) if len(arr) else 0.0,
        "profit_factor": (wins.sum() / abs(losses.sum())) if losses.sum() else float("inf"),
    }


def report(trades: list, args):
    print("\n" + "=" * 64)
    print(f"BACKTEST — {cfg.SYMBOL}   execution {args.exec_tf}"
          f"   COT {'on' if args.with_cot else 'off'}   cost {args.cost_r}R/trade")
    print("=" * 64)
    if not trades:
        print("\nNo trades were triggered over this history.")
        print("That is itself a result: the rules are highly selective. Try a longer")
        print("--bars window, or --exec-tf 30m, before concluding anything.")
        return

    done = [t for t in trades if t["exit"] != "still open at end of data"]
    print(f"\nSignals taken: {len(trades)}   completed: {len(done)}")
    if done:
        span = f'{trades[0]["opened"]:%Y-%m-%d} to {trades[-1]["opened"]:%Y-%m-%d}'
        print(f"Period: {span}")
        longs = sum(1 for t in done if t["dir"] == "LONG")
        print(f"Direction: {longs} long / {len(done)-longs} short")
        print(f"Avg bars held: {np.mean([t['bars'] for t in done]):.0f}")
        print(f"Reached TP1: {100*sum(1 for t in done if 1 in t['hit'])/len(done):.0f}%"
              f"   TP2: {100*sum(1 for t in done if 2 in t['hit'])/len(done):.0f}%"
              f"   TP3: {100*sum(1 for t in done if 3 in t['hit'])/len(done):.0f}%")

    print("\n%-14s %7s %9s %9s %8s %8s" % ("EXIT POLICY", "TRADES", "WIN %", "TOTAL R", "AVG R", "MAXDD"))
    print("-" * 64)
    rows = [("exit at TP1", [policy_r(t, 1, args.cost_r) for t in trades]),
            ("exit at TP2", [policy_r(t, 2, args.cost_r) for t in trades]),
            ("exit at TP3", [policy_r(t, 3, args.cost_r) for t in trades]),
            ("scaled out",  [scaled_r(t, args.cost_r) for t in trades])]
    best = None
    for name, rs in rows:
        s = stats(rs)
        if not s:
            continue
        print("%-14s %7d %8.1f%% %+9.2f %+8.2f %8.2f"
              % (name, s["trades"], s["win_rate"], s["total_r"], s["avg_r"], s["max_dd_r"]))
        if best is None or s["total_r"] > best[1]["total_r"]:
            best = (name, s)

    if best:
        name, s = best
        n = s["trades"]
        print("\n" + "-" * 64)
        print(f"Highest-scoring policy: {name} — {s['total_r']:+.2f}R over {n} trades "
              f"({s['avg_r']:+.2f}R each), worst drawdown {s['max_dd_r']:.2f}R.")

        # Honest verdict. Below a usable sample size, say nothing about expectancy:
        # picking the best of four policies makes even a coin flip look profitable.
        if n < MIN_SAMPLE:
            print(f"\nVERDICT: INCONCLUSIVE — {n} trades is far too few to judge.")
            print("  Picking the best of four exit policies on a sample this small makes")
            print("  even a no-edge strategy look profitable most of the time. Treat the")
            print(f"  numbers above as noise until you have at least {MIN_SAMPLE} trades.")
        else:
            # confidence interval on the mean, so the reader sees the uncertainty
            arr = np.array([r for r in dict(rows)[name] if r is not None])
            se = arr.std(ddof=1) / np.sqrt(n)
            lo, hi = s["avg_r"] - 2 * se, s["avg_r"] + 2 * se
            print(f"\n  95% confidence range for average R: {lo:+.2f} to {hi:+.2f}")
            if lo > 0:
                print("VERDICT: POSITIVE expectancy — the edge survives the uncertainty.")
            elif hi < 0:
                print("VERDICT: NEGATIVE expectancy — these rules lost money here.")
            else:
                print("VERDICT: UNPROVEN — the range includes zero, so no edge is")
                print("  demonstrated either way. More trades needed.")
        print("\nCosts are included. Slippage and weekend gaps are not.")

    # --- do the entries actually add anything over entering at random? ---
    bm = args.__dict__.get("_benchmark")
    if bm:
        real = stats([scaled_r(t, args.cost_r) for t in trades]).get("avg_r")
        pct = 100.0 * float((bm["dist"] < real).mean())
        print("\n" + "-" * 64)
        print("ENTRY-TIMING TEST (same trade count, same long/short mix, random dates)")
        print(f"  random entries : {bm['mean']:+.2f}R average "
              f"(90% of runs between {bm['p5']:+.2f} and {bm['p95']:+.2f})")
        print(f"  your signals   : {real:+.2f}R average")
        edge = real - bm["mean"]
        print(f"  attributable to the market : {bm['mean']:+.2f}R")
        print(f"  attributable to your timing: {edge:+.2f}R   (beats {pct:.0f}% of random runs)")
        if pct >= 95 and edge > 0:
            print("  => The entries add real value beyond the market's drift.")
        elif pct >= 90 and edge > 0:
            print("  => Suggestive but not conclusive at this sample size.")
        else:
            print("  => NO timing edge demonstrated. Random entries did about as well,")
            print("     so the return comes from the market's direction, not the signal.")
            print("     A rising market makes almost any long strategy look profitable.")

    if args.csv:
        rowsout = []
        for t in trades:
            rowsout.append({
                "opened": t["opened"], "dir": t["dir"], "posture": t["posture"],
                "conviction": t["conviction"], "entry": t["entry"], "stop": t["stop"],
                "tp1": t["tps"][0], "tp2": t["tps"][1], "tp3": t["tps"][2],
                "risk": t["risk"], "exit": t["exit"], "closed": t["closed"],
                "bars": t["bars"], "best_R": round(t["mfe_r"], 2),
                "r_tp1": policy_r(t, 1, args.cost_r), "r_tp2": policy_r(t, 2, args.cost_r),
                "r_scaled": scaled_r(t, args.cost_r),
            })
        pd.DataFrame(rowsout).to_csv(args.csv, index=False)
        print(f"\nEvery trade written to {args.csv}")


def main():
    p = argparse.ArgumentParser(description="Backtest the XAU confluence engine.")
    p.add_argument("--exec-tf", default="15m", choices=list(ALL_TFS),
                   help="timeframe that triggers entries (default 15m)")
    p.add_argument("--source", default="yahoo", choices=["yahoo", "twelvedata"],
                   help="yahoo = deep free history, no key (default); twelvedata = your live feed")
    p.add_argument("--bars", type=int, default=2000,
                   help="bars to download (twelvedata only; yahoo returns its maximum)")
    p.add_argument("--cost-r", type=float, default=0.05,
                   help="spread+commission per trade, in R (default 0.05)")
    p.add_argument("--with-cot", action="store_true", help="use real CFTC COT history")
    p.add_argument("--no-trend-filter", action="store_true",
                   help="allow trades against the primary trend (for A/B comparison)")
    p.add_argument("--refresh", action="store_true", help="re-download price data")
    p.add_argument("--csv", help="write every trade to this CSV")
    args = p.parse_args()
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
