"""Turns candles into a per-timeframe reading, then combines all timeframes + macro
into a posture and a trade. This is the same confluence logic as the Confluence Desk."""
from __future__ import annotations
import numpy as np
import pandas as pd

from .models import TFRead, MacroRead, Signal
from . import indicators as ind

WEIGHTS = {"1W": 3.0, "1D": 2.5, "4H": 1.5, "30m": 1.0, "15m": 0.8, "3m": 0.5}
HTF = ("1W", "1D")            # define bank positioning
EXEC = ("3m", "15m")          # fire the entry


def read_from_df(tf: str, df: pd.DataFrame, cfg) -> TFRead:
    """Compute a TFRead from OHLCV candles (data path)."""
    r = TFRead(tf=tf, source="data")
    if df is None or len(df) < cfg.RSI_LEN + cfg.MA_LEN + 2:
        return r
    close = df["close"].values
    rsi = ind.rsi_wilder(close, cfg.RSI_LEN)
    ma, up, lo = ind.rsi_bands(rsi, cfg.MA_LEN, cfg.BB_MULT)
    r.price = float(close[-1])
    r.rsi = _last(rsi)
    r.rsi_ma = _last(ma)
    r.bb_upper = _last(up)
    r.bb_lower = _last(lo)

    highs, lows = ind.find_pivots(df, cfg.SWING_LEN)
    last_high = highs[-1] if highs else None
    last_low = lows[-1] if lows else None
    r.swing_high = last_high["level"] if last_high else None
    r.swing_low = last_low["level"] if last_low else None
    r.sweep, _ = ind.detect_sweep(df, last_high, last_low)
    r.divergence = ind.detect_divergence(df, rsi, (highs, lows))

    # bias: RSI vs 50 and vs its own MA, nudged by band reclaim
    if r.rsi is not None and r.rsi_ma is not None:
        bull = r.rsi > 50 and r.rsi > r.rsi_ma
        bear = r.rsi < 50 and r.rsi < r.rsi_ma
        prev_rsi = rsi[-2] if len(rsi) > 1 else r.rsi
        if r.bb_lower is not None and prev_rsi < r.bb_lower <= r.rsi:
            bull = True            # reclaimed lower band = exhausted sell
        if r.bb_upper is not None and prev_rsi > r.bb_upper >= r.rsi:
            bear = True            # rejected upper band = exhausted buy
        r.bias = "bull" if bull and not bear else "bear" if bear and not bull else "neu"
    return r


def build_signal(reads: dict[str, TFRead], macro: MacroRead, cfg) -> Signal:
    score = 0.0
    tf_bias = {}
    for tf, w in WEIGHTS.items():
        r = reads.get(tf)
        if not r:
            continue
        s = 0.0
        if r.bias == "bull":
            s += w
        elif r.bias == "bear":
            s -= w
        if r.divergence == "bull":
            s += w * 0.8
        elif r.divergence == "bear":
            s -= w * 0.8
        score += s
        tf_bias[tf] = "bull" if s > 0.1 else "bear" if s < -0.1 else "neu"

    # higher-timeframe posture (1W + 1D dominate), confirmed by COT
    htf = 0.0
    for tf in HTF:
        r = reads.get(tf)
        if not r:
            continue
        if r.bias == "bull":
            htf += 2
        elif r.bias == "bear":
            htf -= 2
        if r.divergence == "bull":
            htf += 1.5
        elif r.divergence == "bear":
            htf -= 1.5
    if macro.cot_stance == "bull":
        htf += 2; score += 2
    elif macro.cot_stance == "bear":
        htf -= 2; score -= 2
    if macro.usd_stance == "bull":
        score += 1
    elif macro.usd_stance == "bear":
        score -= 1

    posture = "ACCUMULATION" if htf >= 2.5 else "DISTRIBUTION" if htf <= -2.5 else "ROTATION"
    pdir = 1 if posture == "ACCUMULATION" else -1 if posture == "DISTRIBUTION" else 0

    # execution trigger must agree with posture
    ex_long = ex_short = False
    lvls = None
    for tf in EXEC:
        r = reads.get(tf)
        if not r:
            continue
        if r.sweep == "bull" or (r.bias == "bull" and r.divergence == "bull"):
            ex_long = True
        if r.sweep == "bear" or (r.bias == "bear" and r.divergence == "bear"):
            ex_short = True
        if r.price is not None and lvls is None:
            lvls = r
    # borrow swing levels from 15m if the finest TF lacks them
    if lvls is not None and (lvls.swing_low is None or lvls.swing_high is None):
        r15 = reads.get("15m")
        if r15:
            lvls.swing_low = lvls.swing_low or r15.swing_low
            lvls.swing_high = lvls.swing_high or r15.swing_high

    reasons = [f"Posture {posture} (HTF score {htf:+.1f}, COT {macro.cot_stance})."]
    bias, dir_ = "WAIT", 0
    if pdir > 0 and ex_long:
        bias, dir_ = "LONG", 1
        reasons.append("Execution TF fired a long trigger with posture.")
    elif pdir < 0 and ex_short:
        bias, dir_ = "SHORT", -1
        reasons.append("Execution TF fired a short trigger with posture.")
    elif pdir != 0:
        reasons.append(f"No aligned {'long' if pdir>0 else 'short'} trigger yet — wait for the sweep.")
    else:
        reasons.append("Rotation — no clean institutional footprint. Stand aside.")

    conviction = min(100, int(abs(score) * 8 + (20 if dir_ else 0)))
    if macro.news_red_near:
        conviction = int(conviction * 0.4)
        reasons.append(f"Red-news window ({macro.next_event}) — cut size / skip.")
    if conviction < cfg.MIN_CONVICTION:
        bias = "WAIT"

    sig = Signal(posture=posture, bias=bias, conviction=conviction,
                 reasons=reasons, tf_bias=tf_bias)

    if bias in ("LONG", "SHORT") and lvls and lvls.price is not None:
        entry = lvls.price
        buf = cfg.STOP_BUFFER_PCT * entry
        if bias == "LONG":
            low = lvls.swing_low if lvls.swing_low is not None else entry - buf * 5
            stop = low - buf
            risk = entry - stop
            struct = lvls.swing_high
            tps = [entry + risk,
                   struct if (struct and struct > entry + risk) else entry + 2 * risk,
                   entry + 3 * risk]
        else:
            high = lvls.swing_high if lvls.swing_high is not None else entry + buf * 5
            stop = high + buf
            risk = stop - entry
            struct = lvls.swing_low
            tps = [entry - risk,
                   struct if (struct and struct < entry - risk) else entry - 2 * risk,
                   entry - 3 * risk]
        sig.entry, sig.stop, sig.risk = round(entry, 2), round(stop, 2), round(risk, 2)
        sig.tps = [round(t, 2) for t in tps]
    return sig


def _last(a):
    a = np.asarray(a, dtype=float)
    a = a[~np.isnan(a)]
    return float(a[-1]) if len(a) else None
