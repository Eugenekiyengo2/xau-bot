"""Shared data structures. Both the data path and the vision path emit a TFRead,
so the engine doesn't care where a reading came from."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class TFRead:
    tf: str                       # "1W","1D","4H","30m","15m","3m"
    rsi: Optional[float] = None
    rsi_ma: Optional[float] = None
    bb_upper: Optional[float] = None
    bb_lower: Optional[float] = None
    bias: str = "neu"             # "bull" | "bear" | "neu"
    divergence: str = "none"      # "bull" | "bear" | "none"
    sweep: str = "none"           # "bull" | "bear" | "none"
    price: Optional[float] = None
    swing_low: Optional[float] = None
    swing_high: Optional[float] = None
    source: str = "data"          # "data" | "vision"

    def dict(self):
        return asdict(self)


@dataclass
class MacroRead:
    cot_stance: str = "neu"       # "bull" | "bear" | "neu"
    cot_commercial_net: Optional[float] = None
    cot_mm_net: Optional[float] = None
    cot_commercial_delta: Optional[float] = None
    usd_stance: str = "neu"       # "bull" (USD weak, gold-supportive) ... see note in cot/usd
    news_red_near: bool = False
    next_event: Optional[str] = None
    notes: list = field(default_factory=list)


@dataclass
class Signal:
    posture: str                  # "ACCUMULATION" | "DISTRIBUTION" | "ROTATION"
    bias: str                     # "LONG" | "SHORT" | "WAIT"
    conviction: int
    entry: Optional[float] = None
    stop: Optional[float] = None
    tps: list = field(default_factory=list)
    risk: Optional[float] = None
    reasons: list = field(default_factory=list)
    tf_bias: dict = field(default_factory=dict)
