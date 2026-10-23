"""Vision path: send a chart screenshot to Claude and get back a structured reading.
Use this when you want the bot to read actual TradingView snapshots (with the RSI 9 /
SMA 14 / BB 2 pane and Liquidity Swings visible) instead of pulling raw candles.

Place one image per timeframe in cfg.CHARTS_DIR named 1W.png, 1D.png, 4H.png, 30m.png,
15m.png, 3m.png (png/jpg/webp all fine)."""
from __future__ import annotations
import base64
import json
import os

from .models import TFRead

_MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}

PROMPT = """You are reading a single TradingView XAUUSD chart for the {tf} timeframe.
The lower pane is RSI (length 9, source close) with a 14-period SMA of the RSI and
Bollinger Bands at 2 standard deviations on that RSI. The overlay is LuxAlgo Liquidity
Swings (swing highs/lows drawn at wick extremes, with volume labels).

Return ONLY a compact JSON object, no prose, with these keys:
  "rsi": number            # the current RSI value printed on the pane
  "rsi_vs_ma": "above"|"below"|"flat"
  "bias": "bull"|"bear"|"neu"      # RSI trend vs 50 and its MA on this TF
  "divergence": "bull"|"bear"|"none"   # price vs RSI on the last two swings
  "band": "above_upper"|"below_lower"|"inside"  # where RSI sits vs the 2-sigma band
  "price": number          # current price
  "swing_low": number|null # nearest Liquidity-Swing low (wick) below price
  "swing_high": number|null# nearest Liquidity-Swing high (wick) above price
  "sweep": "bull"|"bear"|"none"  # bull = price swept a swing low then closed back above;
                                 # bear = swept a swing high then closed back below
Read numbers exactly as printed. If a value is not visible, use null."""


def read_chart(tf: str, image_path: str, cfg) -> TFRead:
    import anthropic
    ext = os.path.splitext(image_path)[1].lower()
    media = _MEDIA.get(ext, "image/png")
    with open(image_path, "rb") as f:
        b64 = base64.standard_b64encode(f.read()).decode()

    client = anthropic.Anthropic(api_key=cfg.ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model=cfg.VISION_MODEL,
        max_tokens=600,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}},
                {"type": "text", "text": PROMPT.format(tf=tf)},
            ],
        }],
    )
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    data = _parse_json(text)
    band = data.get("band")
    return TFRead(
        tf=tf, source="vision",
        rsi=_num(data.get("rsi")),
        bias=data.get("bias", "neu"),
        divergence=data.get("divergence", "none"),
        sweep=data.get("sweep", "none"),
        price=_num(data.get("price")),
        swing_low=_num(data.get("swing_low")),
        swing_high=_num(data.get("swing_high")),
        bb_upper=1.0 if band == "above_upper" else None,   # markers; engine mainly uses bias
        bb_lower=1.0 if band == "below_lower" else None,
    )


def read_all(cfg) -> dict[str, TFRead]:
    reads = {}
    for tf in ("1W", "1D", "4H", "30m", "15m", "3m"):
        path = _find(cfg.CHARTS_DIR, tf)
        if not path:
            print(f"[vision] no image for {tf} in {cfg.CHARTS_DIR}")
            continue
        try:
            reads[tf] = read_chart(tf, path, cfg)
        except Exception as e:  # noqa
            print(f"[vision] {tf} failed: {e}")
    return reads


def _find(folder: str, tf: str):
    for ext in _MEDIA:
        p = os.path.join(folder, tf + ext)
        if os.path.exists(p):
            return p
    return None


def _parse_json(text: str):
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):]
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
