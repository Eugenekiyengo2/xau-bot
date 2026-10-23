"""Alert delivery. Telegram is implemented fully; WhatsApp via Twilio is optional."""
from __future__ import annotations
import requests

from .models import Signal, MacroRead


def format_signal(sig: Signal, macro: MacroRead) -> str:
    emoji = {"LONG": "🟢", "SHORT": "🔴", "WAIT": "⚪"}.get(sig.bias, "⚪")
    lines = [
        f"{emoji} XAUUSD — {sig.bias}  ({sig.posture})",
        f"Conviction: {sig.conviction}/100",
    ]
    if sig.bias in ("LONG", "SHORT") and sig.entry is not None:
        lines += [
            "",
            f"Entry : {sig.entry}",
            f"Stop  : {sig.stop}   (risk {sig.risk})",
            f"TP1   : {sig.tps[0]}  (1R)",
            f"TP2   : {sig.tps[1]}",
            f"TP3   : {sig.tps[2]}  (3R)",
        ]
    tb = "  ".join(f"{k}:{v[0].upper()}" for k, v in sig.tf_bias.items())
    if tb:
        lines += ["", f"TF bias: {tb}"]
    lines += ["", "Why:"]
    lines += [f"• {r}" for r in sig.reasons]
    for n in macro.notes:
        lines.append(f"• {n}")
    lines += ["", "Decision aid, not advice. Verify on your chart. Risk only what you can lose."]
    return "\n".join(lines)


def send_telegram(text: str, cfg) -> bool:
    if not (cfg.TELEGRAM_BOT_TOKEN and cfg.TELEGRAM_CHAT_ID):
        print("[alert] Telegram not configured; message below:\n" + text)
        return False
    url = f"https://api.telegram.org/bot{cfg.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": cfg.TELEGRAM_CHAT_ID, "text": text,
                                     "disable_web_page_preview": True}, timeout=30)
        r.raise_for_status()
        return True
    except Exception as e:  # noqa
        print(f"[alert] Telegram failed: {e}")
        return False


def send_whatsapp(text: str, cfg) -> bool:
    """Optional. Requires a Twilio account and `pip install twilio`."""
    if not (cfg.TWILIO_SID and cfg.TWILIO_TOKEN and cfg.TWILIO_FROM and cfg.WHATSAPP_TO):
        return False
    try:
        from twilio.rest import Client
        Client(cfg.TWILIO_SID, cfg.TWILIO_TOKEN).messages.create(
            body=text, from_=cfg.TWILIO_FROM, to=cfg.WHATSAPP_TO)
        return True
    except Exception as e:  # noqa
        print(f"[alert] WhatsApp failed: {e}")
        return False
