"""Turns candles into a per-timeframe reading, then combines all timeframes + macro
into a posture and a trade. This is the same confluence logic as the Confluence Desk."""
from __future__ import annotations
import numpy as np
import pandas as pd

from .models import TFRead, MacroRead, Signal
from . import indicators as ind

WEIGHTS = {"1W": 3.0, "1D": 2.5, "4H": 1.5, "30m": 1.0, "15m": 0.8, "3m": 0.5}
HTF = ("1W", "1D")            # define bank positioning
EXEC = ("3m", "15m")          # default; overridden by cfg.EXEC_TFS when set


def read_from_df(tf: str, df: pd.DataFrame, cfg) -> TFRead:
    """Compute a TFRead from OHLCV candles (data path)."""
    r = TFRead(tf=tf, source="data")
    if df is None or len(df) < cfg.RSI_LEN + cfg.MA_LEN + 2:
        return r
    close = df["close"].values
    rsi = ind.rsi_wilder(close, cfg.RSI_LEN)
    ma, up, lo = ind.rsi_bands(rsi, cfg.MA_LEN, cfg.BB_MULT)
    r.price = float(close[-1])
    if "datetime" in df.columns and len(df):
        r.ts = df["datetime"].iloc[-1]
    r.rsi = _last(rsi)
    r.rsi_ma = _last(ma)
    r.bb_upper = _last(up)
    r.bb_lower = _last(lo)

    r.atr = _last(ind.atr_wilder(df, 14))

    # primary trend: price versus a long moving average
    if len(close) >= cfg.TREND_MA:
        ma = pd.Series(close).rolling(cfg.TREND_MA).mean().iloc[-1]
        if not np.isnan(ma):
            r.trend_up = bool(close[-1] > ma)

    highs, lows = ind.find_pivots(df, cfg.SWING_LEN)
    last_high = highs[-1] if highs else None
    last_low = lows[-1] if lows else None
    r.swing_high = last_high["level"] if last_high else None
    r.swing_low = last_low["level"] if last_low else None
    r.sweep, r.sweep_level = ind.detect_sweep(
        df, last_high, last_low,
        lookback=getattr(cfg, "SWEEP_LOOKBACK", 1),
        atr=r.atr,
        max_drift_atr=getattr(cfg, "SWEEP_MAX_DRIFT_ATR", 2.0))

    # After a grab, look for the confirmation and the precise entry: structure must
    # shift in the new direction, and the imbalance it leaves behind is where to enter.
    if r.sweep in ("bull", "bear"):
        look = getattr(cfg, "SWEEP_LOOKBACK", 30)
        r.sweep_wick, wick_at = ind.sweep_extreme(df, r.sweep, lookback=look)
        r.mss = ind.detect_mss(df, r.sweep, wick_at)
        r.fvg = ind.find_fvg(df, r.sweep, lookback=look, after=wick_at)
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
    """Weigh everything we know, then decide.

    Each input adds or subtracts points toward a direction rather than holding a veto,
    because fifteen conditions that must all agree is a system that never trades. The
    only hard refusals left are the ones that protect capital rather than express an
    opinion: a red-news window, a stop too wide to be worth taking, and a trade that
    fights the primary trend. Everything else argues its case through the score, and
    MIN_CONVICTION is the single dial that decides how convincing is convincing enough.
    """
    factors = []            # (label, points, note) - points are + for long, - for short

    def add(label, pts, note=None):
        if pts:
            factors.append((label, pts, note))

    # --- structure across the timeframes -------------------------------------
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
        add(f"{tf} structure", s)
        tf_bias[tf] = "bull" if s > 0.1 else "bear" if s < -0.1 else "neu"

    # --- what the banks and the macro are doing -------------------------------
    cw = getattr(cfg, "W_COT", 2.0)
    if macro.cot_stance == "bull":
        add("COT (commercials)", +cw, "commercials covering shorts")
    elif macro.cot_stance == "bear":
        add("COT (commercials)", -cw, "commercials adding shorts")

    uw = getattr(cfg, "W_USD", 1.5)
    if macro.usd_stance == "bull":
        add("US dollar", +uw, macro.usd_note)
    elif macro.usd_stance == "bear":
        add("US dollar", -uw, macro.usd_note)

    sw = getattr(cfg, "W_SENTIMENT", 1.0)
    if macro.sentiment_stance == "bull":
        add("retail (faded)", +sw, macro.sentiment_note)
    elif macro.sentiment_stance == "bear":
        add("retail (faded)", -sw, macro.sentiment_note)

    kw = getattr(cfg, "W_SESSION", 1.5)
    if macro.session_bias == "bull":
        add("session cascade", +kw, macro.session_note)
    elif macro.session_bias == "bear":
        add("session cascade", -kw, macro.session_note)

    # --- the trigger: a grab, and whether structure confirmed it ---------------
    ex_long = ex_short = False
    mss_pending = False
    lvls = None
    mw = getattr(cfg, "W_MSS", 2.0)
    for tf in (getattr(cfg, "EXEC_TFS", None) or EXEC):
        r = reads.get(tf)
        if not r:
            continue
        if r.sweep == "bull":
            ex_long = True
            add(f"{tf} sell-side grab", +1.0)
            if r.mss:
                add(f"{tf} structure shift", +mw, "grab confirmed by a break of structure")
            else:
                mss_pending = True
        if r.sweep == "bear":
            ex_short = True
            add(f"{tf} buy-side grab", -1.0)
            if r.mss:
                add(f"{tf} structure shift", -mw, "grab confirmed by a break of structure")
            else:
                mss_pending = True
        if r.bias == "bull" and r.divergence == "bull":
            ex_long = True
        if r.bias == "bear" and r.divergence == "bear":
            ex_short = True
        if r.price is not None and lvls is None:
            lvls = r

    if lvls is not None and (lvls.swing_low is None or lvls.swing_high is None):
        r15 = reads.get("15m")
        if r15:
            lvls.swing_low = lvls.swing_low or r15.swing_low
            lvls.swing_high = lvls.swing_high or r15.swing_high

    score = sum(p for _, p, _ in factors)

    # Posture still describes the higher timeframes, but it no longer vetoes on its own.
    htf = sum(p for lbl, p, _ in factors if lbl.startswith(("1W", "1D"))) + \
          (cw if macro.cot_stance == "bull" else -cw if macro.cot_stance == "bear" else 0)
    posture = "ACCUMULATION" if htf >= 2.5 else "DISTRIBUTION" if htf <= -2.5 else "ROTATION"

    # --- direction -------------------------------------------------------------
    reasons = []
    dir_ = 1 if score > 0 else -1 if score < 0 else 0
    has_trigger = (ex_long and dir_ > 0) or (ex_short and dir_ < 0)
    bias = "LONG" if dir_ > 0 else "SHORT" if dir_ < 0 else "WAIT"

    if not has_trigger:
        bias, dir_ = "WAIT", 0
        if mss_pending:
            reasons.append("Liquidity was grabbed but structure has not shifted yet — "
                           "waiting for confirmation rather than catching the knife.")
        else:
            reasons.append("No entry trigger: nothing has swept liquidity in the "
                           "direction the weight of evidence points.")

    conviction = min(100, int(abs(score) * 7 + (15 if has_trigger else 0)))

    # Volatility does not pick a side; it says how much to trust the reading.
    if macro.vol_regime == "high":
        conviction = int(conviction * 0.75)
        reasons.append(macro.vol_note or "Volatility elevated — conviction reduced.")
    elif macro.vol_regime == "low" and macro.vol_note:
        reasons.append(macro.vol_note)

    # --- hard refusals: capital protection only --------------------------------
    zones = getattr(cfg, "KILLZONES", None)
    if zones and dir_ != 0:
        ts = next((reads[t].ts for t in (getattr(cfg, "EXEC_TFS", None) or EXEC)
                   if reads.get(t) is not None and reads[t].ts is not None), None)
        if ts is not None and not ind.in_killzone(ts, zones):
            import pandas as _pd
            reasons.append(f"Outside the London/New York killzones "
                           f"({_pd.Timestamp(ts):%H:%M} UTC) — flow is thin, standing aside.")
            bias, dir_ = "WAIT", 0

    if getattr(cfg, "USE_TREND_FILTER", False) and dir_ != 0:
        trend = _primary_trend(reads)
        if trend is not None and ((dir_ > 0) != trend):
            reasons.append(f"Blocked: {bias} against the primary "
                           f"{'up' if trend else 'down'}trend ({cfg.TREND_MA}-period MA).")
            bias, dir_ = "WAIT", 0

    if macro.news_red_near:
        conviction = int(conviction * 0.4)
        reasons.append(f"Red-news window ({macro.next_event}) — cut size / skip.")

    if dir_ != 0 and conviction < cfg.MIN_CONVICTION:
        reasons.append(f"Conviction {conviction} below the {cfg.MIN_CONVICTION} threshold.")
        bias, dir_ = "WAIT", 0

    # --- show the working ------------------------------------------------------
    ranked = sorted((f for f in factors if f[1]), key=lambda f: -abs(f[1]))
    top = "; ".join(f"{lbl} {pts:+.1f}" for lbl, pts, _ in ranked[:5])
    reasons.insert(0, f"Score {score:+.1f} ({posture}). Biggest inputs: {top}.")
    for lbl, pts, note in ranked:
        if note:
            reasons.append(f"{lbl} {pts:+.1f}: {note}")

    sig = Signal(posture=posture, bias=bias, conviction=conviction,
                 reasons=reasons, tf_bias=tf_bias,
                 sweep_level=(lvls.sweep_level if lvls is not None else None))

    if bias in ("LONG", "SHORT") and lvls and lvls.price is not None:
        entry = lvls.price
        buf = cfg.STOP_BUFFER_PCT * entry
        # Floor the stop distance. A stop tighter than a fraction of ATR is inside the
        # spread/noise: it would be taken out by ordinary jitter, so it is not a trade.
        min_risk = cfg.MIN_STOP_ATR * lvls.atr if lvls.atr else buf * 2

        precise = getattr(cfg, "PRECISION_ENTRY", False) and lvls.fvg and lvls.sweep_wick

        if bias == "LONG":
            if precise:
                # Wait for the pullback into the imbalance rather than chasing. The stop
                # goes just under the GAP, not all the way down at the grab wick: that is
                # what makes the risk small, and a small risk is the only honest route to
                # a large R multiple. The wick stays the hard invalidation behind it.
                entry = sum(lvls.fvg) / 2.0
                stop = min(lvls.fvg[0] - buf, entry - min_risk)
            else:
                low = lvls.swing_low if lvls.swing_low is not None else entry - buf * 5
                stop = min(low - buf, entry - min_risk)
            risk = entry - stop
            tps = _targets(entry, risk, lvls.swing_high, +1, cfg)
        else:
            if precise:
                entry = sum(lvls.fvg) / 2.0
                stop = max(lvls.fvg[1] + buf, entry + min_risk)
            else:
                high = lvls.swing_high if lvls.swing_high is not None else entry + buf * 5
                stop = max(high + buf, entry + min_risk)
            risk = stop - entry
            tps = _targets(entry, risk, lvls.swing_low, -1, cfg)

        if precise:
            reasons.append(f"Limit entry in the imbalance at {entry:.2f}; "
                           f"invalidation beyond the grab wick. Risk {risk:.2f} "
                           f"instead of chasing at {lvls.price:.2f}.")

        # A stop this wide means the setup has already run too far to be worth taking:
        # the reward per unit of risk is gone even if the direction is right.
        cap = getattr(cfg, "MAX_RISK_ATR", 0) * (lvls.atr or 0)
        if cap and risk > cap:
            sig.bias = "WAIT"
            sig.reasons.append(f"Rejected: stop would be {risk:.2f} wide "
                               f"({risk/lvls.atr:.1f} ATR) — the move has already gone.")
            return sig

        sig.entry, sig.stop, sig.risk = round(entry, 2), round(stop, 2), round(risk, 2)
        sig.tps = [round(t, 2) for t in tps]
    return sig


def _primary_trend(reads: dict):
    """Trend from the slowest timeframe that has one — the weekly outranks the daily."""
    for tf in ("1W", "1D", "4H", "30m"):
        r = reads.get(tf)
        if r is not None and r.trend_up is not None:
            return r.trend_up
    return None


def tp_weights(n: int) -> list:
    """How much of the position comes off at each target.

    Three targets keep the long-standing 50/30/20. Any other count banks half at the
    first target — that is what takes the trade to breakeven and does most of the work —
    and splits the remainder evenly across the rest.
    """
    if n <= 1:
        return [1.0]
    if n == 3:
        return [0.5, 0.3, 0.2]
    return [0.5] + [0.5 / (n - 1)] * (n - 1)


def _targets(entry: float, risk: float, struct, d: int, cfg) -> list:
    """A ladder of cfg.TP_COUNT targets, always ordered in the trade's direction.

    TP1 banks 1R. TP2 aims at the opposing structure, where price is most likely to
    react, clamped so it sits neither on top of TP1 nor out of reach. TP3 sits beyond
    TP2 (or at 3R, whichever is further), and any targets after that step out one R at
    a time. A numbered target nearer than the one before it is a bug, so the ladder is
    built strictly increasing by construction.
    """
    n = max(1, int(getattr(cfg, "TP_COUNT", 3)))
    tp1 = entry + d * risk
    if n == 1:
        return [tp1]

    near = entry + d * cfg.TP2_MIN_R * risk          # TP2 must clear this
    cap = entry + d * cfg.MAX_TP_R * risk            # ...and not exceed this
    if struct is None or d * (struct - near) <= 0:
        tp2 = entry + d * 2.0 * risk                 # no usable structure: plain 2R
    else:
        tp2 = struct if d * (cap - struct) >= 0 else cap
    if n == 2:
        return [tp1, tp2]

    tp3 = tp2 + d * risk                             # always one more R beyond TP2
    stretch = entry + d * 3.0 * risk
    if d * (stretch - tp3) > 0:                      # ...or 3R, whichever is further
        tp3 = stretch

    out = [tp1, tp2, tp3]
    for i in range(1, n - 2):                        # TP4 onwards: one R apart
        out.append(tp3 + d * i * risk)
    return out


def _last(a):
    a = np.asarray(a, dtype=float)
    a = a[~np.isnan(a)]
    return float(a[-1]) if len(a) else None
